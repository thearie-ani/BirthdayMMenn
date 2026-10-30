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
    apply_backup_retention,
    backup_database,
    get_default_database_path,
    list_backups,
    restore_database,
    verify_backup_integrity,
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


def test_verify_backup_integrity_valid_db(tmp_path):
    """Verify integrity check succeeds on a valid, uncorrupted SQLite database."""
    db_file = tmp_path / "valid.db"
    _create_sample_database(db_file)
    assert verify_backup_integrity(db_file) is True


def test_verify_backup_integrity_missing_and_empty(tmp_path):
    """Verify integrity check fails for missing files and 0-byte files."""
    missing = tmp_path / "does_not_exist.db"
    assert verify_backup_integrity(missing) is False

    empty = tmp_path / "empty.db"
    empty.write_text("")
    assert verify_backup_integrity(empty) is False


def test_verify_backup_integrity_corrupted_file(tmp_path):
    """Verify integrity check fails when SQLite file contents are corrupted."""
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("THIS IS NOT A VALID SQLITE DATABASE FILE HEADER")
    assert verify_backup_integrity(corrupt) is False


def test_backup_database_with_verification(tmp_path):
    """Verify backup_database succeeds and passes integrity verification."""
    src_db = tmp_path / "source.db"
    _create_sample_database(src_db)
    backup_dir = tmp_path / "backups"

    backup_file = backup_database(
        source_path=src_db,
        backup_dir=backup_dir,
        verify=True,
    )
    assert backup_file.exists()
    assert verify_backup_integrity(backup_file) is True


def test_apply_backup_retention_pruning(tmp_path):
    """Verify retention policy keeps only the specified count of newest backups."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Create 5 backup files with staggered timestamps
    created_files = []
    for i in range(5):
        f = backup_dir / f"test_db_backup_20261030_12000{i}.db"
        f.write_text(f"backup {i}")
        time.sleep(0.02)
        created_files.append(f)

    # Also add unrelated files that should NEVER be pruned
    unrelated_txt = backup_dir / "notes.txt"
    unrelated_txt.write_text("important user note")
    unrelated_db = backup_dir / "other_database.db"
    unrelated_db.write_text("another database without backup marker")

    # Keep only the 2 newest backups
    pruned = apply_backup_retention(backup_dir=backup_dir, keep_count=2, prefix_stem="test_db")

    # Should prune 3 oldest backups
    assert len(pruned) == 3
    remaining = [f.name for f in backup_dir.iterdir() if f.name.endswith(".db") and "_backup_" in f.name]
    assert len(remaining) == 2
    # The two newest (index 4 and 3) should remain
    assert "test_db_backup_20261030_120004.db" in remaining
    assert "test_db_backup_20261030_120003.db" in remaining

    # Unrelated files must NOT be touched
    assert unrelated_txt.exists()
    assert unrelated_db.exists()


def test_apply_backup_retention_preserves_active_database(tmp_path, monkeypatch):
    """Verify retention policy explicitly protects the active database from deletion."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    active_db = backup_dir / "birthday_mmenn.db"
    _create_sample_database(active_db)

    # Mock get_default_database_path to return active_db
    monkeypatch.setattr("app.database.backup.get_default_database_path", lambda: active_db)

    # Run retention with keep_count=0 (which is a no-op) or keep_count=1
    pruned = apply_backup_retention(backup_dir=backup_dir, keep_count=1)
    assert active_db not in pruned
    assert active_db.exists()


def test_restore_database_success(tmp_path):
    """Verify restoring a database from backup recovers all schema and records accurately."""
    src_db = tmp_path / "original.db"
    backup_dir = tmp_path / "backups"
    _create_sample_database(src_db)

    # 1. Create a valid backup
    backup_file = backup_database(source_path=src_db, backup_dir=backup_dir)

    # 2. Restore into a fresh target path
    restore_target = tmp_path / "restored.db"
    restored = restore_database(backup_path=backup_file, target_path=restore_target, verify_first=True)

    assert restored == restore_target
    assert restore_target.exists()

    # 3. Verify records inside restored database
    conn = sqlite3.connect(restore_target)
    users = conn.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    friends = conn.execute("SELECT id, name FROM friends ORDER BY id").fetchall()
    conn.close()

    assert users == [(1, "Alice"), (2, "Bob")]
    assert friends == [(1, "Charlie"), (2, "Diana")]


def test_restore_database_corrupt_fails_without_modifying_target(tmp_path):
    """Verify restoring a corrupt backup fails fast and does not touch the target."""
    corrupt_backup = tmp_path / "corrupt_backup.db"
    corrupt_backup.write_text("NOT A SQLITE DB")

    target_db = tmp_path / "target.db"
    target_db.write_text("existing target data")

    with pytest.raises(DatabaseBackupError) as exc_info:
        restore_database(backup_path=corrupt_backup, target_path=target_db, verify_first=True)

    assert "integrity" in str(exc_info.value).lower()
    # Target file was preserved
    assert target_db.read_text() == "existing target data"


def test_restore_database_same_source_and_dest_raises_valueerror(tmp_path):
    """Verify passing identical source and destination raises ValueError."""
    dummy_backup = tmp_path / "backup.db"
    _create_sample_database(dummy_backup)

    with pytest.raises(ValueError) as exc_info:
        restore_database(backup_path=dummy_backup, target_path=dummy_backup)

    assert "cannot be the same" in str(exc_info.value).lower()

