"""Foundation and environment sanity tests."""
import importlib
import os
from unittest.mock import patch
from sqlalchemy import create_engine, inspect

from app.config.settings import get_database_url
from app.database.database import init_db, normalize_db_url


def test_required_packages_installed():
    """Verify that all core dependencies can be imported successfully."""
    core_packages = [
        "telegram",
        "sqlalchemy",
        "apscheduler",
        "dotenv",
        "pytest",
    ]
    for package in core_packages:
        module = importlib.import_module(package)
        assert module is not None


def test_env_example_exists():
    """Verify that the template environment file exists."""
    assert os.path.exists(".env.example")


def test_get_database_url_default():
    """Verify that get_database_url returns a default local SQLite path when unset."""
    with patch.dict("os.environ", {"DATABASE_URL": ""}):
        url = get_database_url()
        assert url.startswith("sqlite:///")
        assert "birthday_mate.db" in url


def test_normalize_db_url_converts_aiosqlite():
    """Verify that sqlite+aiosqlite:// is normalized to sqlite:// for sync engine."""
    assert (
        normalize_db_url("sqlite+aiosqlite:///custom.db")
        == "sqlite:///custom.db"
    )
    assert normalize_db_url("sqlite:///standard.db") == "sqlite:///standard.db"


def test_init_db_creates_expected_tables():
    """Verify that init_db creates 'users' and 'friends' tables in the database."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    assert "users" in tables
    assert "friends" in tables
