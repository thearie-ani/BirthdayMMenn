"""
Automated tests for SQLite Database Backup Utility (Stage 12 Step 5).
Uses temporary test databases (via tmp_path) to ensure the production database
is NEVER touched, backed up, or modified during testing.
"""
from pathlib import Path
import sqlite3
import time
import pytest

from app.database.backup import (
    DatabaseBackupError,
    backup_database,
    get_default_database_path,
    list_backups,
)


def _create_sample_database(db_path: Path) -> None:
    """Create a temporary sample SQLite database with tables and records."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, telegram_id INTEGER)")
    cur.execute("CREATE TABLE friends (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, birth_month INT, birth_day INT)")
    cur.execute("INSERT INTO users VALUES (1, 'Alice', 1001), (2, 'Bob', 1002)")
    cur.execute("INSERT INTO friends VALUES (1, 1, 'Charlie', 5, 12), (2, 1, 'Diana', 11, 28)")
    conn.commit()
    conn.close()


def test_backup_database_success(tmp_path):
    """Verify that backup_database creates a valid timestamped backup file in backup_dir."""
    src_db = tmp_path / "test_source.db"
    backup_dir = tmp_path / "backups"
    _create_sample_database(src_db)

    backup_file = backup_database(source_path=src_db, backup_dir=backup_dir)

    assert backup_file.exists()
    assert backup_file.is_file()
    assert backup_file.parent == backup_dir.resolve()
    assert backup_file.name.startswith("test_source_backup_")
    assert backup_file.suffix == ".db"


def test_backup_contains_expected_records(tmp_path):
    """Verify that the backup database contains all expected schema and table rows."""
    src_db = tmp_path / "test_source.db"
    backup_dir = tmp_path / "backups"
    _create_sample_database(src_db)

    backup_file = backup_database(source_path=src_db, backup_dir=backup_dir)

    # Inspect the backup database
    conn = sqlite3.connect(backup_file)
    cur = conn.cursor()

    users = cur.execute("SELECT id, name, telegram_id FROM users ORDER BY id").fetchall()
    assert users == [(1, "Alice", 1001), (2, "Bob", 1002)]

    friends = cur.execute("SELECT id, user_id, name, birth_month, birth_day FROM friends ORDER BY id").fetchall()
    assert friends == [(1, 1, "Charlie", 5, 12), (2, 1, "Diana", 11, 28)]

    conn.close()


def test_original_database_remains_unchanged(tmp_path):
    """Verify that creating a backup does not alter, lock, or corrupt the source database."""
    src_db = tmp_path / "test_source.db"
    backup_dir = tmp_path / "backups"
    _create_sample_database(src_db)

    # Record state before backup
    conn = sqlite3.connect(src_db)
    before_users = conn.execute("SELECT * FROM users").fetchall()
    before_friends = conn.execute("SELECT * FROM friends").fetchall()
    conn.close()

    # Perform backup
    backup_database(source_path=src_db, backup_dir=backup_dir)

    # Verify state after backup is identical and still writable
    conn = sqlite3.connect(src_db)
    after_users = conn.execute("SELECT * FROM users").fetchall()
    after_friends = conn.execute("SELECT * FROM friends").fetchall()

    assert before_users == after_users
    assert before_friends == after_friends

    # Verify source database remains fully functional and writable
    conn.execute("INSERT INTO users VALUES (3, 'Eve', 1003)")
    conn.commit()
    count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    assert count == 3
    conn.close()


def test_missing_source_database_raises_filenotfound(tmp_path):
    """Verify that attempting to backup a non-existent database file raises FileNotFoundError."""
    missing_db = tmp_path / "ghost_database.db"
    backup_dir = tmp_path / "backups"

    with pytest.raises(FileNotFoundError) as exc_info:
        backup_database(source_path=missing_db, backup_dir=backup_dir)

    assert "not found" in str(exc_info.value).lower()


def test_source_path_is_directory_raises_valueerror(tmp_path):
    """Verify passing a directory path as source raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        backup_database(source_path=tmp_path, backup_dir=tmp_path / "out")

    assert "not a file" in str(exc_info.value).lower()


def test_source_and_destination_identical_raises_valueerror(tmp_path):
    """Verify that setting the backup destination equal to the source raises ValueError."""
    src_db = tmp_path / "source.db"
    _create_sample_database(src_db)

    with pytest.raises(ValueError) as exc_info:
        backup_database(
            source_path=src_db,
            backup_dir=tmp_path,
            custom_filename="source.db",
        )

    assert "cannot be the same" in str(exc_info.value).lower()


def test_destination_directory_created_automatically(tmp_path):
    """Verify nested non-existent backup directory is created automatically."""
    src_db = tmp_path / "sample.db"
    _create_sample_database(src_db)

    nested_dir = tmp_path / "deep" / "nested" / "backup_archive"
    assert not nested_dir.exists()

    backup_file = backup_database(source_path=src_db, backup_dir=nested_dir)

    assert nested_dir.exists()
    assert backup_file.exists()
    assert backup_file.parent == nested_dir.resolve()


def test_custom_filename_override(tmp_path):
    """Verify custom filename can be specified rather than auto-timestamped name."""
    src_db = tmp_path / "sample.db"
    _create_sample_database(src_db)
    backup_dir = tmp_path / "backups"

    backup_file = backup_database(
        source_path=src_db,
        backup_dir=backup_dir,
        custom_filename="manual_pre_release_v1.db",
    )

    assert backup_file.name == "manual_pre_release_v1.db"
    assert backup_file.exists()


def test_list_backups_sorted_by_modification_time(tmp_path):
    """Verify list_backups finds all backup databases sorted by mtime."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Create 3 dummy backup files with staggered timestamps
    b1 = backup_dir / "backup_1.db"
    b2 = backup_dir / "backup_2.sqlite"
    b3 = backup_dir / "backup_3.sqlite3"
    non_db = backup_dir / "notes.txt"

    b1.write_text("dummy")
    time.sleep(0.05)
    b2.write_text("dummy")
    time.sleep(0.05)
    b3.write_text("dummy")
    non_db.write_text("ignore me")

    found = list_backups(backup_dir=backup_dir)

    # Non-db file ignored, 3 backup files returned sorted newest first
    assert len(found) == 3
    assert [f.name for f in found] == ["backup_3.sqlite3", "backup_2.sqlite", "backup_1.db"]


def test_list_backups_non_existent_directory(tmp_path):
    """Verify list_backups returns empty list for non-existent directory without crashing."""
    assert list_backups(backup_dir=tmp_path / "does_not_exist") == []
