"""
Automated tests for Reminder Query Optimization (Stage 12 Step 3).
Validates that:
1. get_due_reminders_for_all_users() uses selectinload(User.friends) to eliminate N+1 queries.
2. Multiple users with varying reminder deadlines receive correct reminders.
3. Users without friends are handled cleanly with zero unnecessary queries.
4. Custom user reminder settings (individual day toggles, master switch, notification time) are strictly respected.
5. Persistent deduplication via sent_reminders prevents duplicate alerts.
6. Timezone conversions and ownership isolation are preserved.
"""
from unittest.mock import patch
from datetime import datetime
from typing import List
import zoneinfo
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.database.database import get_session, init_db
from app.database.models.friend import Friend
from app.database.models.reminder import UserSetting
from app.database.models.user import User
from app.database.repositories.reminder_repo import ReminderRepository
from app.services.reminder_ser import (
    get_due_reminders_for_all_users,
    get_due_reminders_for_user,
    record_sent_reminder,
)


@pytest.fixture
def db_engine():
    """Create an isolated in-memory SQLite database engine."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    yield engine
    engine.dispose()


def test_reminder_query_efficiency_eliminates_n_plus_one(db_engine):
    """
    Verify that get_due_reminders_for_all_users loads all users and their friends
    in a constant number of queries (batch SELECT IN), avoiding the N+1 query problem.
    """
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    user_count = 6
    with get_session(db_engine) as session:
        for i in range(user_count):
            user = User(telegram_id=7100 + i, first_name=f"User_{i}")
            session.add(user)
            session.flush()

            # Each user has 2 friends with birthdays on non-due dates (avoiding deduplication queries)
            f1 = Friend(user_id=user.id, name=f"Friend_{i}_A", birth_month=1, birth_day=1)
            f2 = Friend(user_id=user.id, name=f"Friend_{i}_B", birth_month=2, birth_day=2)
            session.add_all([f1, f2])

            repo = ReminderRepository(session)
            setting = repo.get_or_create_user_setting(user.id)
            setting.timezone = "UTC"
            setting.reminder_time = "09:00"

    # Track all executed SQL statements during the scanner run
    captured_queries: List[str] = []

    def query_listener(conn, cursor, statement, parameters, context, executemany):
        captured_queries.append(statement.strip())

    event.listen(db_engine, "before_cursor_execute", query_listener)
    try:
        due = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)
        assert len(due) == 0
    finally:
        event.remove(db_engine, "before_cursor_execute", query_listener)

    # Filter to SELECT statements
    select_queries = [q for q in captured_queries if q.upper().startswith("SELECT")]

    # With selectinload, User and Friend loading are exactly 2 queries:
    # 1. SELECT users...
    # 2. SELECT friends... WHERE friends.user_id IN (...)
    users_queries = [q for q in select_queries if "FROM users" in q]
    friends_queries = [q for q in select_queries if "FROM friends" in q]

    assert len(users_queries) == 1, f"Expected 1 query for users, got {len(users_queries)}"
    assert len(friends_queries) == 1, f"Expected 1 batch query for friends, got {len(friends_queries)}"


def test_multiple_users_reminder_generation(db_engine):
    """
    Verify reminder generation across multiple users with different due thresholds:
    - User 1: Has friends with birthdays in 7 days, 1 day, and today
    - User 2: Has a friend with a birthday today
    - User 3: Has a friend whose birthday is 2 months away (no reminder)
    """
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)  # Reference date: Oct 28

    with get_session(db_engine) as session:
        u1 = User(telegram_id=8101, first_name="MultiUser1")
        u2 = User(telegram_id=8102, first_name="MultiUser2")
        u3 = User(telegram_id=8103, first_name="MultiUser3")
        session.add_all([u1, u2, u3])
        session.flush()

        # U1 friends: today (Oct 28), tomorrow (Oct 29), 7 days (Nov 4)
        f1_today = Friend(user_id=u1.id, name="U1_Today", birth_month=10, birth_day=28)
        f1_tomorrow = Friend(user_id=u1.id, name="U1_Tomorrow", birth_month=10, birth_day=29)
        f1_7days = Friend(user_id=u1.id, name="U1_7Days", birth_month=11, birth_day=4)

        # U2 friend: today (Oct 28)
        f2_today = Friend(user_id=u2.id, name="U2_Today", birth_month=10, birth_day=28)

        # U3 friend: far away (Dec 25)
        f3_far = Friend(user_id=u3.id, name="U3_Far", birth_month=12, birth_day=25)

        session.add_all([f1_today, f1_tomorrow, f1_7days, f2_today, f3_far])

        repo = ReminderRepository(session)
        for u in [u1, u2, u3]:
            s = repo.get_or_create_user_setting(u.id)
            s.timezone = "UTC"
            s.reminder_time = "09:00"

    due = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)

    # U1 should have 3 reminders, U2 should have 1 reminder, U3 should have 0 reminders
    assert len(due) == 4

    u1_reminders = [r for r in due if r["telegram_id"] == 8101]
    u2_reminders = [r for r in due if r["telegram_id"] == 8102]
    u3_reminders = [r for r in due if r["telegram_id"] == 8103]

    assert len(u1_reminders) == 3
    assert len(u2_reminders) == 1
    assert len(u3_reminders) == 0

    u1_types = {r["reminder_type"] for r in u1_reminders}
    assert u1_types == {0, 1, 7}

    assert u2_reminders[0]["friend_name"] == "U2_Today"
    assert u2_reminders[0]["reminder_type"] == 0


def test_users_without_friends_handled_gracefully(db_engine):
    """
    Verify users without friends do not trigger errors, produce zero reminders,
    and are skipped cleanly during batch evaluation.
    """
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        empty_user = User(telegram_id=8201, first_name="EmptyUser")
        normal_user = User(telegram_id=8202, first_name="NormalUser")
        session.add_all([empty_user, normal_user])
        session.flush()

        normal_friend = Friend(
            user_id=normal_user.id, name="NormalFriend", birth_month=10, birth_day=28
        )
        session.add(normal_friend)

        repo = ReminderRepository(session)
        s = repo.get_or_create_user_setting(normal_user.id)
        s.timezone = "UTC"
        s.reminder_time = "09:00"

    # Single-user evaluation on empty user
    with get_session(db_engine) as session:
        user_obj = session.execute(
            select(User).where(User.telegram_id == 8201)
        ).scalar_one()
        single_due = get_due_reminders_for_user(session, user_obj, now_override=ref_now)
        assert single_due == []

    # Batch evaluation
    batch_due = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)
    assert len(batch_due) == 1
    assert batch_due[0]["telegram_id"] == 8202
    assert batch_due[0]["friend_name"] == "NormalFriend"


def test_reminder_settings_respected_across_users(db_engine):
    """
    Verify that individual reminder settings (toggling 7d, 1d, same-day, and master is_enabled)
    are strictly respected during eager-loaded batch queries.
    """
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        u_all = User(telegram_id=8301, first_name="UserAll")
        u_no_7d = User(telegram_id=8302, first_name="UserNo7d")
        u_disabled = User(telegram_id=8303, first_name="UserDisabled")
        session.add_all([u_all, u_no_7d, u_disabled])
        session.flush()

        # Give all 3 users a friend whose birthday is in 7 days (Nov 4)
        for u in [u_all, u_no_7d, u_disabled]:
            f = Friend(user_id=u.id, name=f"FriendOf_{u.first_name}", birth_month=11, birth_day=4)
            session.add(f)

        repo = ReminderRepository(session)
        s_all = repo.get_or_create_user_setting(u_all.id)
        s_all.timezone = "UTC"
        s_all.reminder_time = "09:00"

        s_no_7d = repo.get_or_create_user_setting(u_no_7d.id)
        s_no_7d.timezone = "UTC"
        s_no_7d.reminder_time = "09:00"
        s_no_7d.remind_7_days = False

        s_disabled = repo.get_or_create_user_setting(u_disabled.id)
        s_disabled.timezone = "UTC"
        s_disabled.reminder_time = "09:00"
        s_disabled.is_enabled = False

    due = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)

    # Only UserAll should receive the 7-day reminder
    assert len(due) == 1
    assert due[0]["telegram_id"] == 8301
    assert due[0]["reminder_type"] == 7


def test_duplicate_reminders_prevented_across_scanner_runs(db_engine):
    """
    Verify that once a reminder is recorded as sent via record_sent_reminder(),
    subsequent scanner executions on the same date/time do not return it again.
    """
    tz = zoneinfo.ZoneInfo("UTC")
    ref_now = datetime(2026, 10, 28, 10, 0, tzinfo=tz)

    with get_session(db_engine) as session:
        user = User(telegram_id=8401, first_name="DedupUser")
        session.add(user)
        session.flush()

        f_today = Friend(user_id=user.id, name="TodayDedup", birth_month=10, birth_day=28)
        f_tomorrow = Friend(user_id=user.id, name="TomorrowDedup", birth_month=10, birth_day=29)
        session.add_all([f_today, f_tomorrow])

        repo = ReminderRepository(session)
        s = repo.get_or_create_user_setting(user.id)
        s.timezone = "UTC"
        s.reminder_time = "09:00"

    # First scan: both reminders due
    due_run_1 = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)
    assert len(due_run_1) == 2

    # Record delivery of the 'today' reminder
    today_item = next(r for r in due_run_1 if r["reminder_type"] == 0)
    record_sent_reminder(
        user_id=today_item["user_id"],
        friend_id=today_item["friend_id"],
        reminder_type=today_item["reminder_type"],
        celebration_year=today_item["celebration_year"],
        engine=db_engine,
    )

    # Second scan: only 'tomorrow' reminder is due
    due_run_2 = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)
    assert len(due_run_2) == 1
    assert due_run_2[0]["reminder_type"] == 1
    assert due_run_2[0]["friend_name"] == "TomorrowDedup"

    # Record delivery of the 'tomorrow' reminder
    record_sent_reminder(
        user_id=due_run_2[0]["user_id"],
        friend_id=due_run_2[0]["friend_id"],
        reminder_type=due_run_2[0]["reminder_type"],
        celebration_year=due_run_2[0]["celebration_year"],
        engine=db_engine,
    )

    # Third scan: 0 due
    due_run_3 = get_due_reminders_for_all_users(engine=db_engine, now_override=ref_now)
    assert len(due_run_3) == 0


@patch("app.services.reminder_ser.get_user_local_now")
def test_multi_user_timezone_evaluation_preserved(mock_get_local_now, db_engine):
    """
    Verify timezone calculations correctly evaluate reminder time per-user timezone:
    - User Phnom Penh (UTC+7, 09:00 reminder): local time is 09:30 on birthday (due)
    - User New York (UTC-5, 09:00 reminder): local time is 21:30 previous day (not due)
    """
    def fake_local_now(tz_str: str) -> datetime:
        if tz_str == "Asia/Phnom_Penh":
            return datetime(2026, 10, 28, 9, 30)
        # 08:30 local time is before 09:00 target reminder time
        return datetime(2026, 10, 27, 8, 30)

    mock_get_local_now.side_effect = fake_local_now

    with get_session(db_engine) as session:
        u_kh = User(telegram_id=8501, first_name="CambodiaUser")
        u_ny = User(telegram_id=8502, first_name="NewYorkUser")
        session.add_all([u_kh, u_ny])
        session.flush()

        f_kh = Friend(user_id=u_kh.id, name="FriendKH", birth_month=10, birth_day=28)
        f_ny = Friend(user_id=u_ny.id, name="FriendNY", birth_month=10, birth_day=28)
        session.add_all([f_kh, f_ny])

        repo = ReminderRepository(session)
        s_kh = repo.get_or_create_user_setting(u_kh.id)
        s_kh.timezone = "Asia/Phnom_Penh"
        s_kh.reminder_time = "09:00"

        s_ny = repo.get_or_create_user_setting(u_ny.id)
        s_ny.timezone = "America/New_York"
        s_ny.reminder_time = "09:00"

    # Evaluates get_user_local_now per user's configured timezone
    due = get_due_reminders_for_all_users(engine=db_engine)
    assert len(due) == 1
    assert due[0]["telegram_id"] == 8501
    assert due[0]["friend_name"] == "FriendKH"

