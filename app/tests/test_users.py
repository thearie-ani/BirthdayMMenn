"""
Tests for the User database model and constraints.
"""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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


def test_create_and_retrieve_user(db_engine):
    """Verify that a user can be created, saved, and retrieved with all fields."""
    with get_session(db_engine) as session:
        user = User(
            telegram_id=123456789,
            first_name="Alice",
            username="alice_wonder",
        )
        session.add(user)

    with get_session(db_engine) as session:
        retrieved = session.execute(
            select(User).where(User.telegram_id == 123456789)
        ).scalar_one_or_none()

        assert retrieved is not None
        assert retrieved.id is not None
        assert retrieved.telegram_id == 123456789
        assert retrieved.first_name == "Alice"
        assert retrieved.username == "alice_wonder"
        assert retrieved.created_at is not None
        assert "123456789" in repr(retrieved)


def test_user_telegram_id_unique_constraint(db_engine):
    """Verify that creating two users with the same telegram_id raises IntegrityError."""
    with get_session(db_engine) as session:
        user1 = User(telegram_id=999888777, first_name="User One")
        session.add(user1)

    # Attempt to insert a second user with the identical telegram_id
    with pytest.raises(IntegrityError):
        with get_session(db_engine) as session:
            user2 = User(telegram_id=999888777, first_name="User Two")
            session.add(user2)


def test_user_telegram_id_cannot_be_null(db_engine):
    """Verify that telegram_id cannot be null."""
    with pytest.raises(IntegrityError):
        with get_session(db_engine) as session:
            invalid_user = User(telegram_id=None, first_name="No ID")
            session.add(invalid_user)


def test_user_cascade_deletes_friends(db_engine):
    """Verify that deleting a user removes all friends associated with that user."""
    with get_session(db_engine) as session:
        user = User(telegram_id=555444333, first_name="Parent")
        friend1 = Friend(name="Friend A", birth_month=5, birth_day=10)
        friend2 = Friend(name="Friend B", birth_month=8, birth_day=20)
        user.friends.extend([friend1, friend2])
        session.add(user)

    # Confirm user and friends exist
    with get_session(db_engine) as session:
        user = session.execute(
            select(User).where(User.telegram_id == 555444333)
        ).scalar_one()
        assert len(user.friends) == 2
        user_id = user.id

    # Delete the user
    with get_session(db_engine) as session:
        user_to_delete = session.get(User, user_id)
        session.delete(user_to_delete)

    # Verify both user and their friends are removed from database
    with get_session(db_engine) as session:
        remaining_friends = session.execute(
            select(Friend).where(Friend.user_id == user_id)
        ).scalars().all()
        assert len(remaining_friends) == 0
