"""
Database configuration, session management, and schema initialization.
"""
from contextlib import contextmanager
from typing import Generator, Optional
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import get_database_url


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy database models."""
    pass


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    """
    Enforce foreign key constraints for SQLite connections.

    By default, SQLite does not enforce FOREIGN KEY constraints unless
    'PRAGMA foreign_keys = ON' is executed on every new connection.
    This listener ensures child records cannot reference non-existent parents.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def normalize_db_url(url: Optional[str] = None) -> str:
    """
    Ensure the database URL is compatible with standard SQLAlchemy SQLite drivers.
    If 'sqlite+aiosqlite://' is configured in .env, normalizes it to 'sqlite://'.
    """
    target_url = url or get_database_url()
    if target_url.startswith("sqlite+aiosqlite://"):
        return target_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return target_url


def get_engine(db_url: Optional[str] = None, echo: bool = False) -> Engine:
    """
    Create and configure a new SQLAlchemy Engine.

    Args:
        db_url: Database connection string. If None, loaded from configuration.
        echo: If True, log generated SQL queries.

    Returns:
        SQLAlchemy Engine instance.
    """
    url = normalize_db_url(db_url)
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(url, echo=echo, connect_args=connect_args)


# Singleton instances for default application engine and session factory
_default_engine: Optional[Engine] = None
_default_session_factory: Optional[sessionmaker[Session]] = None


def get_default_engine() -> Engine:
    """Retrieve or initialize the singleton application engine."""
    global _default_engine
    if _default_engine is None:
        _default_engine = get_engine()
    return _default_engine


def get_session_factory(engine: Optional[Engine] = None) -> sessionmaker[Session]:
    """
    Retrieve or create a session factory bound to the specified or default engine.
    """
    global _default_session_factory
    if engine is not None:
        return sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
    if _default_session_factory is None:
        _default_session_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=get_default_engine(), expire_on_commit=False
        )
    return _default_session_factory


@contextmanager
def get_session(engine: Optional[Engine] = None) -> Generator[Session, None, None]:
    """
    Provide a transactional database session scope.

    Yields:
        Active SQLAlchemy Session. Automatically commits on successful exit
        and rolls back on exceptions before closing.
    """
    factory = get_session_factory(engine)
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Optional[Engine] = None) -> None:
    """
    Initialize database tables defined in SQLAlchemy models.

    Args:
        engine: Optional target engine. Uses default engine if omitted.
    """
    # Import all models to ensure they are registered with Base.metadata
    from app.database.models import friend, preference, reminder, user  # noqa: F401

    target_engine = engine or get_default_engine()
    Base.metadata.create_all(bind=target_engine)


def reset_db(engine: Optional[Engine] = None) -> None:
    """
    Drop all existing tables and re-create them.
    Useful for testing and isolated development environments.
    """
    from app.database.models import friend, preference, reminder, user  # noqa: F401

    target_engine = engine or get_default_engine()
    Base.metadata.drop_all(bind=target_engine)
    Base.metadata.create_all(bind=target_engine)
