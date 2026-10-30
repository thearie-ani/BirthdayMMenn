"""
SQLite Database Backup and Recovery Utility for BirthdayMate.
Provides atomic, transactionally safe backups and restorations of the SQLite database
using Python's native sqlite3 Connection.backup() API, with backup verification and retention.
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
DEFAULT_RETENTION_COUNT = 7


class DatabaseBackupError(Exception):
    """Raised when an error occurs during SQLite database backup or recovery execution."""
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


def verify_backup_integrity(backup_path: Union[str, Path]) -> bool:
    """
    Verify the physical and logical integrity of an SQLite database backup file.
    
    Checks:
    1. File exists and is a regular file.
    2. File size is greater than 0 bytes.
    3. Runs SQLite 'PRAGMA integrity_check;' and verifies 'ok' result.

    Args:
        backup_path: Path to the SQLite backup file to verify.

    Returns:
        True if the database file is structurally sound and valid, False otherwise.
    """
    p = Path(backup_path).resolve()
    if not p.exists() or not p.is_file():
        logger.warning("Backup integrity check failed: file not found (%s)", p)
        return False

    try:
        if p.stat().st_size <= 0:
            logger.warning("Backup integrity check failed: file size is 0 bytes (%s)", p)
            return False
    except OSError as err:
        logger.warning("Backup integrity check failed reading file stats: %s", err)
        return False

    conn: Optional[sqlite3.Connection] = None
    try:
        # Open in read-only mode
        try:
            conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10.0)
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            conn = sqlite3.connect(str(p), timeout=10.0)

        cur = conn.cursor()
        res = cur.execute("PRAGMA integrity_check;").fetchone()
        is_ok = bool(res and str(res[0]).strip().lower() == "ok")
        if not is_ok:
            logger.warning("Backup integrity check reported errors: %s", res)
        return is_ok
    except Exception as exc:
        logger.warning("Backup integrity verification encountered an error for %s: %s", p.name, exc)
        return False
    finally:
        if conn is not None:
            conn.close()


def apply_backup_retention(
    backup_dir: Optional[Union[str, Path]] = None,
    keep_count: int = DEFAULT_RETENTION_COUNT,
    prefix_stem: Optional[str] = None,
) -> List[Path]:
    """
    Prune older database backups in the backup directory, keeping the most recent `keep_count`.

    Safety rules:
    - Only removes files matching the backup filename pattern ('*_backup_*.db').
    - Never deletes the active database file.
    - Never deletes non-matching files (e.g. notes, .gitkeep, arbitrary user files).
    - If `keep_count <= 0`, no files are removed.

    Args:
        backup_dir: Directory containing backup files.
        keep_count: Number of most recent backups to preserve.
        prefix_stem: Optional source database stem filter (e.g. 'birthday_mmenn').

    Returns:
        List of Path objects for deleted backup files.
    """
    if keep_count <= 0:
        return []

    target_dir = Path(backup_dir).resolve() if backup_dir is not None else DEFAULT_BACKUP_DIR
    if not target_dir.exists() or not target_dir.is_dir():
        return []

    try:
        active_db_path = get_default_database_path().resolve()
    except Exception:
        active_db_path = None

    # Collect valid backup candidates
    candidates: List[Path] = []
    for item in target_dir.iterdir():
        if not item.is_file():
            continue
        # Protect active database file
        if active_db_path is not None and item.resolve() == active_db_path:
            continue
        # Must match backup naming conventions
        name = item.name
        if not name.endswith(".db"):
            continue
        if "_backup_" not in name and not name.startswith("backup_"):
            continue
        if prefix_stem and not name.startswith(f"{prefix_stem}_backup_"):
            continue

        candidates.append(item)

    # Sort candidates by last modification time (newest first)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    deleted: List[Path] = []
    if len(candidates) > keep_count:
        to_prune = candidates[keep_count:]
        for old_backup in to_prune:
            try:
                old_backup.unlink()
                deleted.append(old_backup)
                logger.info("Pruned old database backup per retention policy: %s", old_backup.name)
            except OSError as err:
                logger.warning("Failed to delete expired backup file %s: %s", old_backup.name, err)

    return deleted


def backup_database(
    source_path: Optional[Union[str, Path]] = None,
    backup_dir: Optional[Union[str, Path]] = None,
    custom_filename: Optional[str] = None,
    pages: int = -1,
    verify: bool = True,
    retention_count: Optional[int] = None,
) -> Path:
    """
    Perform a safe, transaction-consistent online backup of an SQLite database
    using SQLite's native Connection.backup() engine.

    Args:
        source_path: Path to the source SQLite database file. If None, loaded from app config.
        backup_dir: Directory where the backup file will be created. Defaults to 'backups/'.
        custom_filename: Optional specific filename. If omitted, uses timestamped pattern.
        pages: Number of pages to copy per step (-1 copies all pages in a single transaction).
        verify: If True, automatically runs PRAGMA integrity_check on the newly created backup.
        retention_count: If provided, prunes older backups keeping this number of recent files.

    Returns:
        Path to the newly created and verified backup file.

    Raises:
        FileNotFoundError: If the source database file does not exist.
        ValueError: If source and destination paths are identical.
        DatabaseBackupError: If the SQLite backup process or verification fails.
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

    # Step 2: Post-backup integrity check
    if verify:
        if not verify_backup_integrity(dest_path):
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except OSError:
                    pass
            raise DatabaseBackupError(
                f"Newly created backup file failed integrity check: {dest_path.name}"
            )
        logger.info("Backup integrity check PASSED: %s", dest_path.name)

    # Step 3: Apply backup retention if configured
    if retention_count is not None and retention_count > 0:
        apply_backup_retention(
            backup_dir=target_dir,
            keep_count=retention_count,
            prefix_stem=resolved_src.stem,
        )

    return dest_path


def restore_database(
    backup_path: Union[str, Path],
    target_path: Optional[Union[str, Path]] = None,
    verify_first: bool = True,
    pages: int = -1,
) -> Path:
    """
    Safely restore an SQLite database from a verified backup file
    using SQLite's atomic backup engine.

    Args:
        backup_path: Path to the backup file to restore from.
        target_path: Destination path where the database will be restored.
                     Defaults to the configured application database path.
        verify_first: If True, checks PRAGMA integrity_check before restoring.
        pages: Number of pages to copy per step (-1 for single transaction).

    Returns:
        Path to the restored database file.

    Raises:
        FileNotFoundError: If the backup file does not exist.
        ValueError: If backup and target paths are identical.
        DatabaseBackupError: If integrity verification fails or restore encounters errors.
    """
    resolved_backup = Path(backup_path).resolve()
    if not resolved_backup.exists() or not resolved_backup.is_file():
        raise FileNotFoundError(f"Backup file not found: {resolved_backup}")

    if verify_first:
        if not verify_backup_integrity(resolved_backup):
            raise DatabaseBackupError(
                f"Cannot restore: backup file failed integrity verification ({resolved_backup.name})"
            )

    resolved_target = Path(target_path).resolve() if target_path is not None else get_default_database_path()

    if resolved_backup == resolved_target:
        raise ValueError("Backup source and restore destination cannot be the same file.")

    resolved_target.parent.mkdir(parents=True, exist_ok=True)

    src_conn: Optional[sqlite3.Connection] = None
    dst_conn: Optional[sqlite3.Connection] = None

    try:
        try:
            src_conn = sqlite3.connect(
                f"file:{resolved_backup.as_posix()}?mode=ro",
                uri=True,
                timeout=30.0,
            )
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            src_conn = sqlite3.connect(str(resolved_backup), timeout=30.0)

        dst_conn = sqlite3.connect(str(resolved_target), timeout=30.0)

        src_conn.backup(dst_conn, pages=pages)
        logger.info("Successfully restored database to %s from %s", resolved_target.name, resolved_backup.name)
        return resolved_target

    except Exception as exc:
        logger.error("Failed to restore database from backup: %s", exc)
        raise DatabaseBackupError(f"Database restoration failed: {exc}") from exc

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

    parser = argparse.ArgumentParser(description="BirthdayMate SQLite Database Backup & Recovery Utility")
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
        "--keep",
        type=int,
        default=DEFAULT_RETENTION_COUNT,
        help=f"Number of recent backups to retain (default: {DEFAULT_RETENTION_COUNT})",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List existing backup files in the backup directory",
    )
    parser.add_argument(
        "--verify",
        nargs="?",
        const="ALL",
        help="Verify integrity of a specific backup file or ALL in the backup directory",
        default=None,
    )
    parser.add_argument(
        "--restore",
        help="Path to the backup file to restore from",
        default=None,
    )
    parser.add_argument(
        "--to",
        help="Destination path for restoration (default: configured database path)",
        default=None,
    )
    args = parser.parse_args()

    # Mode 1: List backups
    if args.list:
        found = list_backups(backup_dir=args.dest)
        if not found:
            print("No backup files found.")
        else:
            print(f"Found {len(found)} backup(s):")
            for b in found:
                size_kb = b.stat().st_size / 1024
                mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                integrity = "OK" if verify_backup_integrity(b) else "CORRUPT/INVALID"
                print(f" - {b.name} ({size_kb:.1f} KB, modified: {mtime}) [{integrity}]")
        sys.exit(0)

    # Mode 2: Verify backup integrity
    if args.verify:
        if args.verify == "ALL":
            found = list_backups(backup_dir=args.dest)
            if not found:
                print("No backups found to verify.")
                sys.exit(0)
            all_ok = True
            for b in found:
                ok = verify_backup_integrity(b)
                status = "✅ PASS" if ok else "❌ FAIL"
                print(f"{status}: {b.name}")
                if not ok:
                    all_ok = False
            sys.exit(0 if all_ok else 1)
        else:
            ok = verify_backup_integrity(args.verify)
            if ok:
                print(f"✅ Backup integrity verified: {args.verify}")
                sys.exit(0)
            else:
                print(f"❌ Backup verification failed: {args.verify}", file=sys.stderr)
                sys.exit(1)

    # Mode 3: Restore database
    if args.restore:
        try:
            target = restore_database(backup_path=args.restore, target_path=args.to)
            print(f"✅ Database restored successfully:")
            print(f"   Destination: {target}")
            sys.exit(0)
        except Exception as err:
            print(f"❌ Restoration failed: {err}", file=sys.stderr)
            sys.exit(1)

    # Mode 4: Default - Perform backup with retention
    try:
        output_file = backup_database(
            source_path=args.source,
            backup_dir=args.dest,
            verify=True,
            retention_count=args.keep,
        )
        print(f"✅ Database backup created & verified successfully:")
        print(f"   Destination: {output_file}")
        print(f"   Retention: Kept {args.keep} most recent backup(s)")
    except Exception as err:
        print(f"❌ Backup failed: {err}", file=sys.stderr)
        sys.exit(1)
