"""
SQLite Database Backup Utility for BirthdayMate.
Provides atomic, transactionally safe backups of the SQLite database
using Python's native sqlite3 Connection.backup() API.
"""
from datetime import datetime
import logging
import os
from pathlib import Path
import sqlite3
from typing import List, Optional, Union

from app.config.settings import BASE_DIR, get_database_url

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = BASE_DIR / "backups"


class DatabaseBackupError(Exception):
    """Raised when an error occurs during SQLite database backup execution."""
    pass


def get_default_database_path() -> Path:
    """
    Extract the filesystem Path for the configured SQLite database.
    Resolves relative paths against the project root BASE_DIR.
    """
    raw_url = get_database_url()
    clean = raw_url
    for prefix in ["sqlite+aiosqlite:///", "sqlite:///"]:
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break

    db_path = Path(clean)
    if not db_path.is_absolute():
        db_path = (BASE_DIR / db_path).resolve()

    return db_path


def backup_database(
    source_path: Optional[Union[str, Path]] = None,
    backup_dir: Optional[Union[str, Path]] = None,
    custom_filename: Optional[str] = None,
    pages: int = -1,
) -> Path:
    """
    Perform a safe, transaction-consistent online backup of an SQLite database
    using SQLite's native Connection.backup() engine.

    Args:
        source_path: Path to the source SQLite database file. If None, loaded from app config.
        backup_dir: Directory where the backup file will be created. Defaults to 'backups/'.
        custom_filename: Optional specific filename. If omitted, uses timestamped pattern.
        pages: Number of pages to copy per step (-1 copies all pages in a single transaction).

    Returns:
        Path to the newly created backup file.

    Raises:
        FileNotFoundError: If the source database file does not exist.
        ValueError: If source and destination paths are identical.
        DatabaseBackupError: If the SQLite backup process fails.
    """
    resolved_src = Path(source_path).resolve() if source_path is not None else get_default_database_path()

    if not resolved_src.exists():
        raise FileNotFoundError(f"Source database file not found: {resolved_src}")

    if not resolved_src.is_file():
        raise ValueError(f"Source database path is not a file: {resolved_src}")

    target_dir = Path(backup_dir).resolve() if backup_dir is not None else DEFAULT_BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    if custom_filename:
        dest_filename = custom_filename
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_filename = f"{resolved_src.stem}_backup_{timestamp}.db"

    dest_path = target_dir / dest_filename

    if resolved_src == dest_path:
        raise ValueError("Backup destination cannot be the same as the source database.")

    src_conn: Optional[sqlite3.Connection] = None
    dst_conn: Optional[sqlite3.Connection] = None

    try:
        # Open source in read-only mode to guarantee the source file cannot be altered
        try:
            src_conn = sqlite3.connect(
                f"file:{resolved_src.as_posix()}?mode=ro",
                uri=True,
                timeout=30.0,
            )
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            # Fallback to standard connect if URI read-only syntax is unsupported on the platform
            src_conn = sqlite3.connect(str(resolved_src), timeout=30.0)

        dst_conn = sqlite3.connect(str(dest_path), timeout=30.0)

        # Execute atomic backup using SQLite's native backup API
        src_conn.backup(dst_conn, pages=pages)

        logger.info("Successfully created database backup: %s", dest_path.name)
        return dest_path

    except Exception as exc:
        logger.error("Failed to create database backup: %s", exc)
        # Clean up partial backup file if an error occurred during write
        if dest_path.exists():
            try:
                dest_path.unlink()
            except OSError:
                pass
        raise DatabaseBackupError(f"Database backup failed: {exc}") from exc

    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()


def list_backups(backup_dir: Optional[Union[str, Path]] = None) -> List[Path]:
    """
    List all database backup files in the backup directory,
    sorted by last modification time (most recent first).
    """
    target_dir = Path(backup_dir).resolve() if backup_dir is not None else DEFAULT_BACKUP_DIR
    if not target_dir.exists() or not target_dir.is_dir():
        return []

    backups = [
        f for f in target_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
    ]
    return sorted(backups, key=lambda p: p.stat().st_mtime, reverse=True)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="BirthdayMate SQLite Database Backup Utility")
    parser.add_argument(
        "--source",
        help="Path to the source database file (default: configured database path)",
        default=None,
    )
    parser.add_argument(
        "--dest",
        help="Target backup directory (default: 'backups/')",
        default=None,
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List existing backup files in the backup directory",
    )
    args = parser.parse_args()

    if args.list:
        found = list_backups(backup_dir=args.dest)
        if not found:
            print("No backup files found.")
        else:
            print(f"Found {len(found)} backup(s):")
            for b in found:
                size_kb = b.stat().st_size / 1024
                mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                print(f" - {b.name} ({size_kb:.1f} KB, modified: {mtime})")
        sys.exit(0)

    try:
        output_file = backup_database(source_path=args.source, backup_dir=args.dest)
        print(f"✅ Database backup created successfully:")
        print(f"   Destination: {output_file}")
    except Exception as err:
        print(f"❌ Backup failed: {err}", file=sys.stderr)
        sys.exit(1)
