"""
Tests for Stage 8: Automatic Birthday Reminders, persistent deduplication,
timezone calculations, JobQueue delivery, failure isolation, and interactive Settings.
"""
from datetime import date, datetime
import zoneinfo
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from telegram.error import Forbidden, NetworkError
from telegram.ext import ConversationHandler

from app.bot.handlers.settings import (
    CANCEL_SETTINGS_MESSAGE,
    UNREGISTERED_SETTINGS_MESSAGE,
    WAITING_FOR_SETTINGS_TIME,
    WAITING_FOR_SETTINGS_TIMEZONE,
    cancel_settings_input,
    format_settings_panel_text,
    get_quick_timezone_keyboard,
    get_settings_conversation_handler,
    get_settings_inline_keyboard,
    open_settings_command,
    process_change_time,
    process_change_timezone_text,
    process_quick_timezone_callback,
    settings_callback_handler,
    start_change_time,
    start_change_timezone,
)
from app.database.database import get_session, init_db
from app.database.models.friend import Friend
from app.database.models.reminder import SentReminder, UserSetting
from app.database.models.user import User
from app.database.repositories.reminder_repo import ReminderRepository
from app.main import create_bot_application
from app.scheduler.birthday_sch import (
    JOB_NAME,
    check_and_send_birthday_reminders,
    setup_birthday_scheduler,
)
from app.services.friend_ser import UserNotRegisteredError
from app.services.reminder_ser import (
    build_reminder_message,
    get_due_reminders_for_all_users,
    get_due_reminders_for_user,
    get_user_settings_dto,
    record_sent_reminder,
    toggle_user_setting_dto,
    update_user_reminder_time_dto,
    update_user_timezone_dto,
)
from app.utils.timezone import (
    DEFAULT_REMINDER_TIME,
    DEFAULT_TIMEZONE,
    get_safe_timezone,
    get_user_local_date,
    is_reminder_time_reached,
    is_valid_timezone,
    parse_reminder_time,
    validate_reminder_time_format,
    validate_timezone_input,
)


@pytest.fixture
def db_engine():
    """Create an isolated in-memory SQLite database engine."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def thread_safe_engine():
    """Create an isolated in-memory SQLite engine for cross-thread async tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    yield engine
    engine.dispose()


# ==============================================================================
# Timezone & Utility Tests
# ==============================================================================


def test_timezone_validation():
    """Verify is_valid_timezone handles valid and invalid IANA names."""
    assert is_valid_timezone("Asia/Phnom_Penh") is True
    assert is_valid_timezone("UTC") is True
    assert is_valid_timezone("America/New_York") is True
    assert is_valid_timezone("Invalid/Timezone") is False
    assert is_valid_timezone("") is False
    assert is_valid_timezone(None) is False


def test_safe_timezone_fallback():
    """Verify get_safe_timezone falls back to DEFAULT_TIMEZONE on invalid inputs."""
    safe_tz = get_safe_timezone("Invalid/Zone")
    assert safe_tz.key == DEFAULT_TIMEZONE

    valid_tz = get_safe_timezone("America/New_York")
    assert valid_tz.key == "America/New_York"


def test_parse_reminder_time():
    """Verify parse_reminder_time correctly parses valid HH:MM and falls back on invalid."""
    assert parse_reminder_time("09:00") == (9, 0)
    assert parse_reminder_time("18:30") == (18, 30)
    assert parse_reminder_time("invalid") == (9, 0)
    assert parse_reminder_time(None) == (9, 0)
    assert parse_reminder_time("25:00") == (9, 0)


def test_is_reminder_time_reached():
    """Verify is_reminder_time_reached checks if local time is at or past target time."""
    tz = zoneinfo.ZoneInfo("UTC")
    time_before = datetime(2026, 10, 28, 8, 59, tzinfo=tz)
    time_exact = datetime(2026, 10, 28, 9, 0, tzinfo=tz)
    time_after = datetime(2026, 10, 28, 14, 30, tzinfo=tz)

    assert is_reminder_time_reached(time_before, "09:00") is False
    assert is_reminder_time_reached(time_exact, "09:00") is True
    assert is_reminder_time_reached(time_after, "09:00") is True


def test_different_timezone_local_dates():
    """Verify that different timezones can be on different calendar dates simultaneously."""
    # When it's 2026-10-28 23:30 in Cambodia (UTC+7), it is 2026-10-28 16:30 in UTC
    phnom_penh_tz = zoneinfo.ZoneInfo("Asia/Phnom_Penh")
    ny_tz = zoneinfo.ZoneInfo("America/New_York")

    # Construct explicit localized datetimes
    dt_pp = datetime(2026, 10, 29, 2, 0, tzinfo=phnom_penh_tz)
    dt_ny = dt_pp.astimezone(ny_tz)

    assert dt_pp.date() == date(2026, 10, 29)
    assert dt_ny.date() == date(2026, 10, 28)


# ==============================================================================
# Reminder Calculation & Message Formatting Tests
# ==============================================================================


def test_build_reminder_message_formatting():
    """Verify formatting for 7-day, 1-day, and same-day reminder messages."""
    # 7-day with age
    msg_7_age = build_reminder_message("Alice", 11, 4, 1995, 7, 2026)
    assert "Alice's birthday is in 7 days on November 4! They will be turning 31." in msg_7_age

    # 1-day without age
    msg_1_no_age = build_reminder_message("Bob", 10, 29, None, 1, 2026)
    assert "Bob's birthday is tomorrow, October 29!" in msg_1_no_age
    assert "turning" not in msg_1_no_age.lower()

    # Same day with age
    msg_0_age = build_reminder_message("Charlie", 10, 28, 2000, 0, 2026)
    assert "Today is Charlie's birthday (October 28)! They are turning 26 today! 🎂🥳" in msg_0_age


def test_reminder_trigger_offsets(db_engine):
    """Verify that only 7 days, 1 day, and 0 days trigger reminders; 2, 5, 8 do not."""
    tz = zoneinfo.ZoneInfo("UTC")
    # Reference localized now: 2026-10-28 at 10:00 (past 09:00)
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        user = User(telegram_id=1001, first_name="Owner")
        session.add(user)
        session.flush()

        # Add friends with different offsets
        f_today = Friend(user_id=user.id, name="TodayFriend", birth_month=10, birth_day=28)
        f_tomorrow = Friend(user_id=user.id, name="TomorrowFriend", birth_month=10, birth_day=29)
        f_2days = Friend(user_id=user.id, name="TwoDaysFriend", birth_month=10, birth_day=30)
        f_5days = Friend(user_id=user.id, name="FiveDaysFriend", birth_month=11, birth_day=2)
        f_7days = Friend(user_id=user.id, name="SevenDaysFriend", birth_month=11, birth_day=4)
        f_8days = Friend(user_id=user.id, name="EightDaysFriend", birth_month=11, birth_day=5)
        session.add_all([f_today, f_tomorrow, f_2days, f_5days, f_7days, f_8days])

    with get_session(db_engine) as session:
        user = session.execute(select(User).where(User.telegram_id == 1001)).scalar_one()
        due = get_due_reminders_for_user(session, user, now_override=ref_now)

        due_names = [d["friend_name"] for d in due]
        assert "TodayFriend" in due_names
        assert "TomorrowFriend" in due_names
        assert "SevenDaysFriend" in due_names
        assert "TwoDaysFriend" not in due_names
        assert "FiveDaysFriend" not in due_names
        assert "EightDaysFriend" not in due_names
        assert len(due) == 3


def test_december_january_crossover_reminder(db_engine):
    """Verify 7-day and 1-day reminders work across year boundary (Dec 30 -> Jan 6 and Jan 1)."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 12, 30, 9, 30, tzinfo=tz)

    with get_session(db_engine) as session:
        user = User(telegram_id=1002, first_name="YearEndUser")
        session.add(user)
        session.flush()

        f_cross_7 = Friend(user_id=user.id, name="Jan6Friend", birth_month=1, birth_day=6)
        f_cross_2 = Friend(user_id=user.id, name="Jan1Friend", birth_month=1, birth_day=1)
        session.add_all([f_cross_7, f_cross_2])

    with get_session(db_engine) as session:
        user = session.execute(select(User).where(User.telegram_id == 1002)).scalar_one()
        due = get_due_reminders_for_user(session, user, now_override=ref_now)

        assert len(due) == 1
        assert due[0]["friend_name"] == "Jan6Friend"
        assert due[0]["reminder_type"] == 7
        assert due[0]["celebration_year"] == 2027


def test_leap_day_reminders_non_leap_and_leap_years(db_engine):
    """Verify February 29 birthday evaluates on Feb 28 in non-leap year (2025) and Feb 29 in leap year (2028)."""
    tz = zoneinfo.ZoneInfo("UTC")

    # Case 1: Non-leap year 2025
    ref_2025 = datetime(2025, 2, 28, 9, 15, tzinfo=tz)
    with get_session(db_engine) as session:
        user = User(telegram_id=1003, first_name="LeapOwner")
        session.add(user)
        session.flush()
        f_leap = Friend(user_id=user.id, name="LeapFriend", birth_month=2, birth_day=29, birth_year=2000)
        session.add(f_leap)

    with get_session(db_engine) as session:
        user = session.execute(select(User).where(User.telegram_id == 1003)).scalar_one()
        due_2025 = get_due_reminders_for_user(session, user, now_override=ref_2025)
        assert len(due_2025) == 1
        assert due_2025[0]["reminder_type"] == 0
        assert "February 28" in due_2025[0]["message_text"]
        assert "turning 25" in due_2025[0]["message_text"].lower()

    # Case 2: Leap year 2028
    ref_2028 = datetime(2028, 2, 29, 9, 15, tzinfo=tz)
    with get_session(db_engine) as session:
        user = session.execute(select(User).where(User.telegram_id == 1003)).scalar_one()
        due_2028 = get_due_reminders_for_user(session, user, now_override=ref_2028)
        assert len(due_2028) == 1
        assert due_2028[0]["reminder_type"] == 0
        assert "February 29" in due_2028[0]["message_text"]
        assert "turning 28" in due_2028[0]["message_text"].lower()


# ==============================================================================
# Persistent Deduplication & Restart Simulation Tests
# ==============================================================================


def test_persistent_deduplication_across_scans(db_engine):
    """Verify that once a reminder is recorded as sent, subsequent scans skip it."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        user = User(telegram_id=2001, first_name="DedupUser")
        session.add(user)
        session.flush()
        friend = Friend(user_id=user.id, name="BdayBoy", birth_month=10, birth_day=28)
        session.add(friend)

    # First scan: reminder is due
    with get_session(db_engine) as session:
        user = session.execute(select(User).where(User.telegram_id == 2001)).scalar_one()
        due = get_due_reminders_for_user(session, user, now_override=ref_now)
        assert len(due) == 1

        # Simulate Telegram delivery and persistence
        repo = ReminderRepository(session)
        repo.record_sent_reminder(
            user_id=user.id,
            friend_id=due[0]["friend_id"],
            reminder_type=due[0]["reminder_type"],
            celebration_year=due[0]["celebration_year"],
        )

    # Second scan: reminder is already sent in database, so 0 are due
    with get_session(db_engine) as session:
        user = session.execute(select(User).where(User.telegram_id == 2001)).scalar_one()
        due_again = get_due_reminders_for_user(session, user, now_override=ref_now)
        assert len(due_again) == 0


def test_deduplication_persists_across_application_restart(db_engine):
    """Simulate a bot restart: newly created repository/session instances preserve sent logs."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 11, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        user = User(telegram_id=2002, first_name="RestartUser")
        session.add(user)
        session.flush()
        friend = Friend(user_id=user.id, name="RestartFriend", birth_month=10, birth_day=29)
        session.add(friend)
        session.flush()

        # Pre-record sent reminder (as if sent before restart)
        repo = ReminderRepository(session)
        repo.record_sent_reminder(user.id, friend.id, reminder_type=1, celebration_year=2026)

    # "Restart": Open brand new session and repository
    with get_session(db_engine) as fresh_session:
        fresh_user = fresh_session.execute(select(User).where(User.telegram_id == 2002)).scalar_one()
        fresh_repo = ReminderRepository(fresh_session)
        assert fresh_repo.is_reminder_sent(friend_id=friend.id, celebration_year=2026, reminder_type=1) is True

        due = get_due_reminders_for_user(fresh_session, fresh_user, now_override=ref_now)
        assert len(due) == 0


# ==============================================================================
# User Settings & Preference Controls
# ==============================================================================


def test_global_disable_reminders(db_engine):
    """Verify that when is_enabled=False, no reminders are returned."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        user = User(telegram_id=3001, first_name="DisabledUser")
        session.add(user)
        session.flush()
        friend = Friend(user_id=user.id, name="IgnoredFriend", birth_month=10, birth_day=28)
        session.add(friend)

        repo = ReminderRepository(session)
        settings = repo.get_or_create_user_setting(user.id)
        settings.is_enabled = False

    with get_session(db_engine) as session:
        user = session.execute(select(User).where(User.telegram_id == 3001)).scalar_one()
        due = get_due_reminders_for_user(session, user, now_override=ref_now)
        assert len(due) == 0


def test_individual_reminder_type_preferences(db_engine):
    """Verify disabling 7-day reminder skips it while still allowing 1-day and same-day reminders."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        user = User(telegram_id=3002, first_name="SelectiveUser")
        session.add(user)
        session.flush()
        # 7 days away (Nov 4)
        f7 = Friend(user_id=user.id, name="F7", birth_month=11, birth_day=4)
        session.add(f7)

        repo = ReminderRepository(session)
        settings = repo.get_or_create_user_setting(user.id)
        settings.remind_7_days = False

    with get_session(db_engine) as session:
        user = session.execute(select(User).where(User.telegram_id == 3002)).scalar_one()
        due = get_due_reminders_for_user(session, user, now_override=ref_now)
        assert len(due) == 0


# ==============================================================================
# Multi-User Data Isolation
# ==============================================================================


def test_multi_user_reminder_isolation(db_engine):
    """Verify User A's friend reminder is strictly addressed to User A and never to User B."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        user_a = User(telegram_id=4001, first_name="UserA")
        user_b = User(telegram_id=4002, first_name="UserB")
        session.add_all([user_a, user_b])
        session.flush()

        # User A has a friend with a birthday today
        f_a = Friend(user_id=user_a.id, name="FriendOfA", birth_month=10, birth_day=28)
        # User B has no friend with a birthday today
        f_b = Friend(user_id=user_b.id, name="FriendOfB", birth_month=12, birth_day=25)
        session.add_all([f_a, f_b])

    due_all = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)
    assert len(due_all) == 1
    assert due_all[0]["telegram_id"] == 4001
    assert due_all[0]["user_id"] == user_a.id
    assert due_all[0]["friend_name"] == "FriendOfA"


# ==============================================================================
# Newly Added Friend Dynamic Discovery (No Manual Rescheduling)
# ==============================================================================


def test_newly_added_friend_dynamic_discovery(db_engine):
    """Verify adding a new friend dynamically causes the next scanner check to discover it."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        user = User(telegram_id=5001, first_name="DynamicUser")
        session.add(user)

    # Initial scan: 0 due
    due_1 = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)
    assert len(due_1) == 0

    # User adds a friend whose birthday is today
    with get_session(db_engine) as session:
        user = session.execute(select(User).where(User.telegram_id == 5001)).scalar_one()
        new_friend = Friend(user_id=user.id, name="InstantFriend", birth_month=10, birth_day=28)
        session.add(new_friend)

    # Second scan: dynamically discovered without any scheduler reconfiguration
    due_2 = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)
    assert len(due_2) == 1
    assert due_2[0]["friend_name"] == "InstantFriend"


# ==============================================================================
# Scheduler Delivery & Error Handling Tests
# ==============================================================================


@pytest.mark.anyio
async def test_scheduler_delivery_success(thread_safe_engine):
    """Verify check_and_send_birthday_reminders sends Telegram message and records persistent log."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=6001, first_name="SchedulerUser")
        session.add(user)
        session.flush()
        friend = Friend(user_id=user.id, name="Celebrant", birth_month=10, birth_day=28)
        session.add(friend)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    mock_context = MagicMock()
    mock_context.bot = mock_bot
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "now_override": ref_now,
    }

    await check_and_send_birthday_reminders(mock_context)

    # Verify Telegram message sent
    mock_bot.send_message.assert_awaited_once()
    _, kwargs = mock_bot.send_message.call_args
    assert kwargs["chat_id"] == 6001
    assert "Today is Celebrant's birthday" in kwargs["text"]

    # Verify persistent database sent log was recorded
    with get_session(thread_safe_engine) as session:
        sent_logs = session.execute(select(SentReminder)).scalars().all()
        assert len(sent_logs) == 1
        assert sent_logs[0].friend_id == friend.id
        assert sent_logs[0].reminder_type == 0


@pytest.mark.anyio
async def test_scheduler_delivery_failure_does_not_log_sent(thread_safe_engine):
    """Verify if Telegram send_message fails, the reminder is NOT recorded as sent."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=6002, first_name="BlockedUser")
        session.add(user)
        session.flush()
        friend = Friend(user_id=user.id, name="Celebrant2", birth_month=10, birth_day=28)
        session.add(friend)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=Forbidden("Forbidden: bot was blocked by the user"))

    mock_context = MagicMock()
    mock_context.bot = mock_bot
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "now_override": ref_now,
    }

    await check_and_send_birthday_reminders(mock_context)

    mock_bot.send_message.assert_awaited_once()

    # Verify SentReminder was NOT saved in database
    with get_session(thread_safe_engine) as session:
        sent_logs = session.execute(select(SentReminder)).scalars().all()
        assert len(sent_logs) == 0


@pytest.mark.anyio
async def test_scheduler_delivery_failure_isolation_between_users(thread_safe_engine):
    """Verify an error sending to User A does not stop User B from receiving their reminder."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(thread_safe_engine) as session:
        user1 = User(telegram_id=6003, first_name="FailingUser")
        user2 = User(telegram_id=6004, first_name="SucceedingUser")
        session.add_all([user1, user2])
        session.flush()
        f1 = Friend(user_id=user1.id, name="F1", birth_month=10, birth_day=28)
        f2 = Friend(user_id=user2.id, name="F2", birth_month=10, birth_day=28)
        session.add_all([f1, f2])

    async def mock_send(chat_id, text):
        if chat_id == 6003:
            raise NetworkError("Simulated network drop")
        return MagicMock()

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=mock_send)

    mock_context = MagicMock()
    mock_context.bot = mock_bot
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "now_override": ref_now,
    }

    await check_and_send_birthday_reminders(mock_context)

    assert mock_bot.send_message.await_count == 2

    # User 2's sent log is recorded, User 1's is not
    with get_session(thread_safe_engine) as session:
        sent_logs = session.execute(select(SentReminder)).scalars().all()
        assert len(sent_logs) == 1
        assert sent_logs[0].user_id == user2.id


def test_scheduler_setup_prevents_duplicate_jobs():
    """Verify setup_birthday_scheduler does not register multiple jobs when called repeatedly."""
    dummy_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    app = create_bot_application(dummy_token)

    if app.job_queue is not None:
        jobs = app.job_queue.get_jobs_by_name(JOB_NAME)
        assert len(jobs) == 1

        # Attempt repeated registration
        setup_birthday_scheduler(app)
        jobs_after = app.job_queue.get_jobs_by_name(JOB_NAME)
        assert len(jobs_after) == 1


# ==============================================================================
# Stage 8.2 Tests: Settings Validators, DTO Services, and Keyboards
# ==============================================================================


def test_validate_reminder_time_format_valid():
    """Verify validate_reminder_time_format correctly accepts and standardizes 24-hr times."""
    cases = [
        ("09:00", "09:00"),
        ("9:00", "09:00"),
        ("00:00", "00:00"),
        ("23:59", "23:59"),
        ("14:30", "14:30"),
        (" 07:05 ", "07:05"),
    ]
    for raw, expected in cases:
        valid, val, err = validate_reminder_time_format(raw)
        assert valid is True
        assert val == expected
        assert err is None


def test_validate_reminder_time_format_invalid():
    """Verify validate_reminder_time_format rejects malformed, out-of-range, and empty inputs."""
    invalid_cases = [
        "24:00",
        "12:60",
        "-1:00",
        "abc",
        "9",
        "9:5",
        "12:00:00",
        "",
        "   ",
        None,
    ]
    for raw in invalid_cases:
        valid, val, err = validate_reminder_time_format(raw)
        assert valid is False
        assert err is not None


def test_validate_timezone_input_valid():
    """Verify validate_timezone_input accepts exact IANA names, case-insensitive names, and common aliases."""
    cases = [
        ("Asia/Phnom_Penh", "Asia/Phnom_Penh"),
        ("UTC", "UTC"),
        ("America/New_York", "America/New_York"),
        ("Europe/London", "Europe/London"),
        ("asia/phnom_penh", "Asia/Phnom_Penh"),
        ("utc", "UTC"),
        ("Phnom Penh", "Asia/Phnom_Penh"),
        ("bangkok", "Asia/Bangkok"),
        ("London", "Europe/London"),
        ("New York", "America/New_York"),
        ("Tokyo", "Asia/Tokyo"),
    ]
    for raw, expected in cases:
        valid, val, err = validate_timezone_input(raw)
        assert valid is True, f"Failed for {raw}"
        assert val == expected
        assert err is None


def test_validate_timezone_input_invalid():
    """Verify validate_timezone_input rejects unknown, malformed, or blank timezones."""
    invalid_cases = [
        "Mars/Crater",
        "InvalidZone",
        "GMT+7",
        "Not_A_Timezone",
        "",
        "   ",
        None,
        "12345",
    ]
    for raw in invalid_cases:
        valid, val, err = validate_timezone_input(raw)
        assert valid is False
        assert err is not None


def test_get_user_settings_dto_new_and_existing(db_engine):
    """Verify get_user_settings_dto automatically seeds default settings for a new user."""
    with get_session(db_engine) as session:
        user = User(telegram_id=9001, first_name="SettingTester")
        session.add(user)

    # 1. First fetch creates default settings
    dto = get_user_settings_dto(telegram_id=9001, engine=db_engine)
    assert dto["is_enabled"] is True
    assert dto["remind_7_days"] is True
    assert dto["remind_1_day"] is True
    assert dto["remind_same_day"] is True
    assert dto["reminder_time"] == "09:00"
    assert dto["timezone"] == "Asia/Phnom_Penh"

    # 2. Second fetch returns existing record
    dto2 = get_user_settings_dto(telegram_id=9001, engine=db_engine)
    assert dto2["id"] == dto["id"]


def test_get_user_settings_dto_unregistered(db_engine):
    """Verify get_user_settings_dto raises UserNotRegisteredError for unknown user."""
    with pytest.raises(UserNotRegisteredError):
        get_user_settings_dto(telegram_id=99999999, engine=db_engine)


def test_toggle_user_setting_dto(db_engine):
    """Verify toggle_user_setting_dto toggles boolean fields and persists state."""
    with get_session(db_engine) as session:
        user = User(telegram_id=9002, first_name="ToggleUser")
        session.add(user)

    # Toggle is_enabled: True -> False -> True
    dto1 = toggle_user_setting_dto(telegram_id=9002, field_name="is_enabled", engine=db_engine)
    assert dto1["is_enabled"] is False

    dto2 = toggle_user_setting_dto(telegram_id=9002, field_name="is_enabled", engine=db_engine)
    assert dto2["is_enabled"] is True

    # Toggle individual reminder tiers
    dto_7d = toggle_user_setting_dto(telegram_id=9002, field_name="remind_7_days", engine=db_engine)
    assert dto_7d["remind_7_days"] is False

    dto_1d = toggle_user_setting_dto(telegram_id=9002, field_name="remind_1_day", engine=db_engine)
    assert dto_1d["remind_1_day"] is False

    dto_0d = toggle_user_setting_dto(telegram_id=9002, field_name="remind_same_day", engine=db_engine)
    assert dto_0d["remind_same_day"] is False

    # Invalid field name raises ValueError
    with pytest.raises(ValueError):
        toggle_user_setting_dto(telegram_id=9002, field_name="non_existent", engine=db_engine)


def test_update_user_reminder_time_dto(db_engine):
    """Verify update_user_reminder_time_dto validates and persists time changes."""
    with get_session(db_engine) as session:
        user = User(telegram_id=9003, first_name="TimeUser")
        session.add(user)

    dto = update_user_reminder_time_dto(telegram_id=9003, raw_time="18:30", engine=db_engine)
    assert dto["reminder_time"] == "18:30"

    # Single digit hour gets standardized
    dto2 = update_user_reminder_time_dto(telegram_id=9003, raw_time="8:00", engine=db_engine)
    assert dto2["reminder_time"] == "08:00"

    # Invalid time raises ValueError
    with pytest.raises(ValueError):
        update_user_reminder_time_dto(telegram_id=9003, raw_time="25:00", engine=db_engine)


def test_update_user_timezone_dto(db_engine):
    """Verify update_user_timezone_dto validates and persists timezone changes."""
    with get_session(db_engine) as session:
        user = User(telegram_id=9004, first_name="TzUser")
        session.add(user)

    dto = update_user_timezone_dto(telegram_id=9004, raw_tz="Europe/Berlin", engine=db_engine)
    assert dto["timezone"] == "Europe/Berlin"

    # Alias normalization
    dto_alias = update_user_timezone_dto(telegram_id=9004, raw_tz="Tokyo", engine=db_engine)
    assert dto_alias["timezone"] == "Asia/Tokyo"

    # Invalid timezone raises ValueError
    with pytest.raises(ValueError):
        update_user_timezone_dto(telegram_id=9004, raw_tz="Moon/Base", engine=db_engine)


def test_settings_panel_format_and_keyboards():
    """Verify formatting functions render emojis and buttons correctly."""
    mock_settings = {
        "is_enabled": True,
        "remind_7_days": True,
        "remind_1_day": False,
        "remind_same_day": True,
        "reminder_time": "09:00",
        "timezone": "Asia/Phnom_Penh",
    }
    panel = format_settings_panel_text(mock_settings)
    assert "⚙️ Birthday Reminder Settings" in panel
    assert "🔔 Enabled (Active)" in panel
    assert "7 Days Before: ✅ Enabled" in panel
    assert "1 Day Before: ❌ Disabled" in panel
    assert "09:00" in panel
    assert "Asia/Phnom_Penh" in panel

    keyboard = get_settings_inline_keyboard(mock_settings)
    buttons = [btn.text for row in keyboard.inline_keyboard for btn in row]
    assert "🔕 Disable All" in buttons
    assert "7 Days: ✅ ON" in buttons
    assert "1 Day: ❌ OFF" in buttons
    assert "Today: ✅ ON" in buttons
    assert "⏰ Change Time" in buttons
    assert "🌍 Change Timezone" in buttons
    assert "🔙 Back to Main Menu" in buttons

    quick_tz = get_quick_timezone_keyboard()
    tz_buttons = [btn.text for row in quick_tz.inline_keyboard for btn in row]
    assert "Asia/Phnom_Penh" in tz_buttons
    assert "UTC" in tz_buttons
    assert "Asia/Bangkok" in tz_buttons


# ==============================================================================
# Stage 8.2 Tests: Telegram Settings Handlers and Conversation Flow
# ==============================================================================


@pytest.mark.anyio
async def test_open_settings_command_registered(thread_safe_engine):
    """Verify open_settings_command displays settings panel for registered user."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=9101, first_name="OpenSettingsUser")
        session.add(user)

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 9101

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await open_settings_command(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "⚙️ Birthday Reminder Settings" in kwargs["text"]
    assert kwargs["reply_markup"] is not None


@pytest.mark.anyio
async def test_open_settings_command_unregistered(thread_safe_engine):
    """Verify open_settings_command replies with register prompt for unregistered user."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 99998888

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await open_settings_command(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == UNREGISTERED_SETTINGS_MESSAGE


@pytest.mark.anyio
async def test_settings_callback_handler_toggle_in_place(thread_safe_engine):
    """Verify settings_callback_handler toggles settings and edits panel in place."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=9102, first_name="CallbackUser")
        session.add(user)

    mock_query = MagicMock()
    mock_query.data = "settings_toggle_7d"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = 9102

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await settings_callback_handler(mock_update, mock_context)

    mock_query.answer.assert_awaited_once()
    mock_query.edit_message_text.assert_awaited_once()
    _, kwargs = mock_query.edit_message_text.call_args
    assert "7 Days Before: ❌ Disabled" in kwargs["text"]


@pytest.mark.anyio
async def test_settings_callback_back_to_main_menu():
    """Verify settings_back_main sends the main menu keyboard."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_query = MagicMock()
    mock_query.data = "settings_back_main"
    mock_query.message = mock_message
    mock_query.answer = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = 9103

    mock_context = MagicMock()

    await settings_callback_handler(mock_update, mock_context)

    mock_query.answer.assert_awaited_once()
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "Main Menu" in kwargs["text"]


@pytest.mark.anyio
async def test_time_conversation_flow(thread_safe_engine):
    """Verify entering new reminder time updates DB and restores settings panel."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=9104, first_name="TimeFlowUser")
        session.add(user)

    # 1. Start change time
    mock_start_msg = MagicMock()
    mock_start_msg.reply_text = AsyncMock()

    mock_query = MagicMock()
    mock_query.message = mock_start_msg
    mock_query.answer = AsyncMock()

    mock_update_start = MagicMock()
    mock_update_start.callback_query = mock_query
    mock_update_start.message = None

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    state = await start_change_time(mock_update_start, mock_context)
    assert state == WAITING_FOR_SETTINGS_TIME
    mock_start_msg.reply_text.assert_awaited_once()

    # 2. Enter invalid time
    mock_invalid_msg = MagicMock()
    mock_invalid_msg.text = "25:30"
    mock_invalid_msg.reply_text = AsyncMock()

    mock_update_invalid = MagicMock()
    mock_update_invalid.message = mock_invalid_msg
    mock_update_invalid.effective_user.id = 9104

    next_state = await process_change_time(mock_update_invalid, mock_context)
    assert next_state == WAITING_FOR_SETTINGS_TIME
    assert "⚠️" in mock_invalid_msg.reply_text.call_args[1]["text"]

    # 3. Enter valid time
    mock_valid_msg = MagicMock()
    mock_valid_msg.text = "14:15"
    mock_valid_msg.reply_text = AsyncMock()

    mock_update_valid = MagicMock()
    mock_update_valid.message = mock_valid_msg
    mock_update_valid.effective_user.id = 9104

    end_state = await process_change_time(mock_update_valid, mock_context)
    assert end_state == ConversationHandler.END

    # Check DB was updated
    dto = get_user_settings_dto(telegram_id=9104, engine=thread_safe_engine)
    assert dto["reminder_time"] == "14:15"


@pytest.mark.anyio
async def test_timezone_conversation_flow(thread_safe_engine):
    """Verify entering new timezone via text or quick button updates DB."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=9105, first_name="TzFlowUser")
        session.add(user)

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    # 1. Start change timezone
    mock_start_msg = MagicMock()
    mock_start_msg.reply_text = AsyncMock()

    mock_query = MagicMock()
    mock_query.message = mock_start_msg
    mock_query.answer = AsyncMock()

    mock_update_start = MagicMock()
    mock_update_start.callback_query = mock_query
    mock_update_start.message = None

    state = await start_change_timezone(mock_update_start, mock_context)
    assert state == WAITING_FOR_SETTINGS_TIMEZONE
    mock_start_msg.reply_text.assert_awaited_once()

    # 2. Enter invalid text
    mock_invalid_msg = MagicMock()
    mock_invalid_msg.text = "NonExistent/City"
    mock_invalid_msg.reply_text = AsyncMock()

    mock_update_invalid = MagicMock()
    mock_update_invalid.message = mock_invalid_msg
    mock_update_invalid.effective_user.id = 9105

    next_state = await process_change_timezone_text(mock_update_invalid, mock_context)
    assert next_state == WAITING_FOR_SETTINGS_TIMEZONE
    assert "⚠️" in mock_invalid_msg.reply_text.call_args[1]["text"]

    # 3. Enter valid text
    mock_valid_msg = MagicMock()
    mock_valid_msg.text = "America/New_York"
    mock_valid_msg.reply_text = AsyncMock()

    mock_update_valid = MagicMock()
    mock_update_valid.message = mock_valid_msg
    mock_update_valid.effective_user.id = 9105

    end_state = await process_change_timezone_text(mock_update_valid, mock_context)
    assert end_state == ConversationHandler.END

    dto = get_user_settings_dto(telegram_id=9105, engine=thread_safe_engine)
    assert dto["timezone"] == "America/New_York"

    # 4. Quick button selection
    mock_quick_query = MagicMock()
    mock_quick_query.data = "tz_set:Asia/Tokyo"
    mock_quick_query.answer = AsyncMock()
    mock_quick_query.message = MagicMock()
    mock_quick_query.message.reply_text = AsyncMock()

    mock_update_quick = MagicMock()
    mock_update_quick.callback_query = mock_quick_query
    mock_update_quick.effective_user.id = 9105

    quick_end_state = await process_quick_timezone_callback(mock_update_quick, mock_context)
    assert quick_end_state == ConversationHandler.END

    dto_quick = get_user_settings_dto(telegram_id=9105, engine=thread_safe_engine)
    assert dto_quick["timezone"] == "Asia/Tokyo"


@pytest.mark.anyio
async def test_settings_cancel_input():
    """Verify cancel_settings_input restores the main menu."""
    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_msg
    mock_update.callback_query = None

    mock_context = MagicMock()

    res = await cancel_settings_input(mock_update, mock_context)
    assert res == ConversationHandler.END
    mock_msg.reply_text.assert_awaited_once()
    kwargs = mock_msg.reply_text.call_args[1]
    assert kwargs["text"] == CANCEL_SETTINGS_MESSAGE


# ==============================================================================
# Stage 8.2 Tests: Reminder Engine Respects User Preferences & Isolation
# ==============================================================================


@pytest.mark.anyio
async def test_engine_respects_settings_toggles(thread_safe_engine):
    """Verify reminder engine honors is_enabled, remind_7_days, remind_1_day, and remind_same_day."""
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 9, 30, tzinfo=tz)

    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=9201, first_name="ToggledAlertUser")
        session.add(user)
        session.flush()

        # Add 3 friends: same-day (Oct 28), 1-day (Oct 29), 7-day (Nov 4)
        f_today = Friend(user_id=user.id, name="TodayFriend", birth_month=10, birth_day=28)
        f_1day = Friend(user_id=user.id, name="TomorrowFriend", birth_month=10, birth_day=29)
        f_7day = Friend(user_id=user.id, name="NextWeekFriend", birth_month=11, birth_day=4)
        session.add_all([f_today, f_1day, f_7day])

        # Default settings: timezone=UTC, all enabled
        repo = ReminderRepository(session)
        setting = repo.get_or_create_user_setting(user.id)
        setting.timezone = "UTC"
        setting.reminder_time = "09:00"

    # 1. With all enabled, all 3 reminders are due
    due_all = get_due_reminders_for_all_users(thread_safe_engine, now_override=ref_now)
    assert len(due_all) == 3

    # 2. Disable 7-day reminder
    with get_session(thread_safe_engine) as session:
        s = session.query(UserSetting).filter_by(user_id=user.id).one()
        s.remind_7_days = False

    due_no_7d = get_due_reminders_for_all_users(thread_safe_engine, now_override=ref_now)
    assert len(due_no_7d) == 2
    types = {r["reminder_type"] for r in due_no_7d}
    assert 7 not in types
    assert 1 in types
    assert 0 in types

    # 3. Disable 1-day reminder
    with get_session(thread_safe_engine) as session:
        s = session.query(UserSetting).filter_by(user_id=user.id).one()
        s.remind_1_day = False

    due_only_today = get_due_reminders_for_all_users(thread_safe_engine, now_override=ref_now)
    assert len(due_only_today) == 1
    assert due_only_today[0]["reminder_type"] == 0

    # 4. Disable master switch (is_enabled=False)
    with get_session(thread_safe_engine) as session:
        s = session.query(UserSetting).filter_by(user_id=user.id).one()
        s.is_enabled = False

    due_disabled = get_due_reminders_for_all_users(thread_safe_engine, now_override=ref_now)
    assert len(due_disabled) == 0


@pytest.mark.anyio
async def test_engine_respects_custom_notification_time(thread_safe_engine):
    """Verify reminder engine honors custom reminder_time (e.g. 14:00 vs 09:00)."""
    tz = zoneinfo.ZoneInfo("UTC")

    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=9202, first_name="CustomTimeUser")
        session.add(user)
        session.flush()

        friend = Friend(user_id=user.id, name="SameDayBuddy", birth_month=10, birth_day=28)
        session.add(friend)

        repo = ReminderRepository(session)
        setting = repo.get_or_create_user_setting(user.id)
        setting.timezone = "UTC"
        setting.reminder_time = "14:00"

    # At 09:30 UTC: target 14:00 not reached -> 0 reminders
    morning = datetime(2026, 10, 28, 9, 30, tzinfo=tz)
    due_morning = get_due_reminders_for_all_users(thread_safe_engine, now_override=morning)
    assert len(due_morning) == 0

    # At 14:05 UTC: target 14:00 reached -> 1 reminder due
    afternoon = datetime(2026, 10, 28, 14, 5, tzinfo=tz)
    due_afternoon = get_due_reminders_for_all_users(thread_safe_engine, now_override=afternoon)
    assert len(due_afternoon) == 1
    assert due_afternoon[0]["friend_name"] == "SameDayBuddy"


@pytest.mark.anyio
async def test_multi_user_settings_and_delivery_isolation(thread_safe_engine):
    """Verify User A's custom settings never affect User B's settings or reminder schedule."""
    tz = zoneinfo.ZoneInfo("UTC")
    now_utc = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(thread_safe_engine) as session:
        user_a = User(telegram_id=9301, first_name="UserA")
        user_b = User(telegram_id=9302, first_name="UserB")
        session.add_all([user_a, user_b])
        session.flush()

        f_a = Friend(user_id=user_a.id, name="FriendA", birth_month=10, birth_day=28)
        f_b = Friend(user_id=user_b.id, name="FriendB", birth_month=10, birth_day=28)
        session.add_all([f_a, f_b])

        repo = ReminderRepository(session)
        # User A disables reminders completely
        s_a = repo.get_or_create_user_setting(user_a.id)
        s_a.timezone = "UTC"
        s_a.is_enabled = False

        # User B keeps default active settings
        s_b = repo.get_or_create_user_setting(user_b.id)
        s_b.timezone = "UTC"
        s_b.is_enabled = True

    due = get_due_reminders_for_all_users(thread_safe_engine, now_override=now_utc)

    # Only User B receives the reminder
    assert len(due) == 1
    assert due[0]["telegram_id"] == 9302
    assert due[0]["friend_name"] == "FriendB"


def test_create_bot_application_registers_settings_handlers():
    """Verify create_bot_application registers settings message, command, callback, and conversation handlers."""
    dummy_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    app = create_bot_application(dummy_token)

    handlers = app.handlers.get(0, [])

    # Check commands
    command_names = [h.commands for h in handlers if hasattr(h, "commands")]
    flattened_commands = [cmd for sub in command_names for cmd in sub]
    assert "settings" in flattened_commands

    # Check conversation handler for settings
    conv_handlers = [h for h in handlers if isinstance(h, ConversationHandler)]
    assert len(conv_handlers) >= 3
