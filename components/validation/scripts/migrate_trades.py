#!/usr/bin/env python3
"""Database migration for Phase 1 validation fields.

Provides safe, idempotent migration to add latency and fill tracking columns
to the trades database.

Usage:
    python3 migrate_trades.py                  # Run migration
    python3 migrate_trades.py --check          # Check status only

Pitfall: ALTER TABLE auto-commits in SQLite. Do NOT call conn.execute("COMMIT").
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Default path to trades database
_DEFAULT_DB_PATH = Path(__file__).parent.parent.parent.parent / "research" / "trades.db"

# Phase 1 validation columns: (column_name, column_definition)
_PHASE1_COLUMNS: List[Tuple[str, str]] = [
    ("detection_delay_ms", "INTEGER DEFAULT 0"),
    ("execution_delay_ms", "INTEGER DEFAULT 0"),
    ("fill_delay_ms", "INTEGER DEFAULT 0"),
    ("total_latency_ms", "INTEGER DEFAULT 0"),
    ("intended_entry_price", "REAL"),
    ("actual_fill_price", "REAL"),
    ("slippage_bps", "REAL DEFAULT 0"),
    ("fill_completion_pct", "REAL DEFAULT 100"),
    ("snapshot_id", "TEXT"),
]


def get_existing_columns(conn: sqlite3.Connection) -> List[str]:
    """Get list of existing column names in the trades table.
    
    Args:
        conn: SQLite connection to the database.
        
    Returns:
        List of column names.
    """
    cursor = conn.execute("PRAGMA table_info(trades)")
    return [row[1] for row in cursor.fetchall()]


def migrate_db(db_path: Optional[Path] = None) -> bool:
    """Migrate trades database to add Phase 1 validation columns.
    
    Checks if each column exists before adding it with ALTER TABLE.
    Safe to run multiple times - will not add duplicate columns.
    
    Args:
        db_path: Path to trades.db. Defaults to research/trades.db.
        
    Returns:
        True if migration succeeded (or no migration needed), False on failure.
    """
    db = db_path if db_path else _DEFAULT_DB_PATH

    if not db.exists():
        # No database file exists - nothing to migrate
        # Schema will be created with all columns on first use
        print(f"[MIGRATE] Database not found: {db}")
        return True

    conn = None
    try:
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")

        # Check if trades table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
        )
        if not cursor.fetchone():
            print("[MIGRATE] No trades table found")
            return True

        # Get existing columns
        existing_columns = set(get_existing_columns(conn))
        print(f"[MIGRATE] Existing columns: {len(existing_columns)}")

        # Add missing columns
        columns_added = 0
        for col_name, col_def in _PHASE1_COLUMNS:
            if col_name not in existing_columns:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_def}")
                columns_added += 1
                print(f"[MIGRATE] Added column: {col_name}")

        # ALTER TABLE auto-commits in SQLite
        # No explicit COMMIT needed - it will raise an error
        
        print(f"[MIGRATE] Migration complete: {columns_added} columns added")
        return True

    except Exception as e:
        print(f"[MIGRATE] Failed to migrate trades DB: {e}")
        return False

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def check_migration_status(db_path: Optional[Path] = None) -> dict:
    """Check migration status without modifying database.
    
    Args:
        db_path: Path to trades.db. Defaults to research/trades.db.
        
    Returns:
        Dict with migration status details.
    """
    db = db_path if db_path else _DEFAULT_DB_PATH
    
    result = {
        "db_path": str(db),
        "exists": False,
        "has_trades_table": False,
        "columns_present": [],
        "columns_missing": list(col for col, _ in _PHASE1_COLUMNS),
        "migration_needed": False,
    }

    if not db.exists():
        return result

    result["exists"] = True

    conn = None
    try:
        conn = sqlite3.connect(str(db))

        # Check if trades table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
        )
        if not cursor.fetchone():
            return result

        result["has_trades_table"] = True

        # Get existing columns
        existing_columns = set(get_existing_columns(conn))

        # Check which Phase 1 columns are present
        result["columns_present"] = [
            col for col, _ in _PHASE1_COLUMNS if col in existing_columns
        ]
        result["columns_missing"] = [
            col for col, _ in _PHASE1_COLUMNS if col not in existing_columns
        ]
        result["migration_needed"] = len(result["columns_missing"]) > 0

        return result

    except Exception as e:
        print(f"[MIGRATE] Failed to check migration status: {e}")
        return result

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    # Run migration when executed directly
    check_only = "--check" in sys.argv
    
    print("Checking migration status...")
    status = check_migration_status()

    print(f"  Database exists: {status['exists']}")
    print(f"  Has trades table: {status['has_trades_table']}")
    print(f"  Columns present: {status['columns_present']}")
    print(f"  Columns missing: {status['columns_missing']}")
    print(f"  Migration needed: {status['migration_needed']}")

    if check_only:
        sys.exit(0 if not status["migration_needed"] else 1)

    if status["migration_needed"]:
        print("\nRunning migration...")
        success = migrate_db()
        if success:
            print("Migration completed successfully.")
        else:
            print("Migration FAILED.")
            sys.exit(1)
    else:
        print("\nNo migration needed.")