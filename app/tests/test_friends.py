"""
Tests for the Friend database model, relationships, and birthday representations.
"""
from datetime import date
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError

from app.database.database import get_session, init_db
from app.database.models.friend import Friend
from app.database.models.user import User


@pytest.fixture
def db_engine():
    """Create an in-memory SQLite database engine for testing."""
    engine = create_engine("sqlite:///:memory:")
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
