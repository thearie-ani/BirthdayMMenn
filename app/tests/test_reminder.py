"""
Tests for Stage 8.1: Automatic Birthday Reminders, persistent deduplication,
timezone calculations, JobQueue delivery, and failure isolation.
"""
from datetime import date, datetime
import zoneinfo
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from telegram.error import Forbidden, NetworkError

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
from app.services.reminder_ser import (
    build_reminder_message,
    get_due_reminders_for_all_users,
    get_due_reminders_for_user,
    record_sent_reminder,
)
from app.utils.timezone import (
    DEFAULT_REMINDER_TIME,
    DEFAULT_TIMEZONE,
    get_safe_timezone,
    get_user_local_date,
    is_reminder_time_reached,
    is_valid_timezone,
    parse_reminder_time,
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
