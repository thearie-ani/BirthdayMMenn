"""
Automated unit tests for Container Health Check probe (Stage 13 Step 4).
Tests process check, database accessibility, storage write checks, and exit codes.
"""
from pathlib import Path
import sqlite3
import pytest

from app.healthcheck import (
    check_database_health,
    check_process_health,
    check_storage_health,
    run_healthcheck,
)


def test_check_process_health_passes():
    """Verify check_process_health returns True in running environment."""
    assert check_process_health() is True


def test_check_database_health_with_valid_database(tmp_path, monkeypatch):
    """Verify check_database_health returns True when database is valid and readable."""
    db_file = tmp_path / "health_check.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE t (id INT)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.healthcheck.get_default_database_path", lambda: db_file)
    assert check_database_health() is True


def test_check_database_health_fresh_startup_writable_dir(tmp_path, monkeypatch):
    """Verify check_database_health returns True on fresh startup when target dir is writable."""
    uncreated_db = tmp_path / "fresh_startup.db"
    monkeypatch.setattr("app.healthcheck.get_default_database_path", lambda: uncreated_db)
    assert check_database_health() is True


def test_check_database_health_fails_on_corrupt_database(tmp_path, monkeypatch):
    """Verify check_database_health returns False when database file is corrupted."""
    corrupt_db = tmp_path / "corrupt.db"
    corrupt_db.write_text("CORRUPTED BYTES")

    monkeypatch.setattr("app.healthcheck.get_default_database_path", lambda: corrupt_db)
    assert check_database_health() is False


def test_check_storage_health_passes(tmp_path, monkeypatch):
    """Verify check_storage_health returns True when parent directory is writable."""
    target_db = tmp_path / "data" / "app.db"
    target_db.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.healthcheck.get_default_database_path", lambda: target_db)
    assert check_storage_health() is True


def test_run_healthcheck_returns_zero_when_healthy(tmp_path, monkeypatch):
    """Verify run_healthcheck returns 0 exit code when all subsystems are healthy."""
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE t (id INT)")
    conn.close()

    monkeypatch.setattr("app.healthcheck.get_default_database_path", lambda: db_file)
    assert run_healthcheck() == 0


def test_run_healthcheck_returns_one_when_unhealthy(tmp_path, monkeypatch):
    """Verify run_healthcheck returns 1 exit code when database is corrupted."""
    bad_db = tmp_path / "bad.db"
    bad_db.write_text("NOT A DATABASE")

    monkeypatch.setattr("app.healthcheck.get_default_database_path", lambda: bad_db)
    assert run_healthcheck() == 1
