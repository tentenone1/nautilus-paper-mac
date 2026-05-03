"""Whale discovery database layer."""
import sqlite3
import time
import os
from datetime import datetime, timezone

from pipeline.config import DB_PATH, DATA_DIR


def get_connection() -> sqlite3.Connection:
    """Get a connection to the whale discovery database."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Initialize the database schema."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS whales (
            address TEXT PRIMARY KEY,
            name TEXT,
            alpha_score REAL DEFAULT 0,
            pnl REAL DEFAULT 0,
            volume REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            total_trades INTEGER DEFAULT 0,
            market_category TEXT DEFAULT 'unknown',
            last_seen TEXT,
            tags TEXT DEFAULT '[]',
            discovered_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS whale_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            whale_address TEXT NOT NULL,
            whale_name TEXT,
            alpha_score REAL DEFAULT 0,
            market_slug TEXT,
            market_title TEXT,
            market_category TEXT DEFAULT '',
            condition_id TEXT,
            token_id TEXT,
            outcome TEXT,
            side TEXT,
            size REAL,
            price REAL,
            usd_value REAL,
            confidence REAL,
            detected_at TEXT,
            signaled INTEGER DEFAULT 0,
            UNIQUE(whale_address, market_slug, token_id, outcome, side)
        );

        CREATE TABLE IF NOT EXISTS scan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TEXT,
            scan_type TEXT,
            wallets_found INTEGER DEFAULT 0,
            signals_found INTEGER DEFAULT 0,
            errors TEXT,
            duration_seconds REAL
        );

        CREATE TABLE IF NOT EXISTS seen_positions (
            whale_address TEXT,
            asset_id TEXT,
            outcome TEXT,
            seen_at TEXT,
            expires_at TEXT,
            PRIMARY KEY (whale_address, asset_id, outcome)
        );
    """)
    conn.commit()
    conn.close()


def upsert_whale(address: str, name: str, alpha_score: float = 0,
                 pnl: float = 0, volume: float = 0, win_rate: float = 0,
                 total_trades: int = 0, tags: str = "[]",
                 market_category: str = "unknown") -> None:
    """Insert or update a whale record."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute("""
        INSERT INTO whales (address, name, alpha_score, pnl, volume,
                           win_rate, total_trades, market_category, tags, last_seen,
                           discovered_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
            (SELECT discovered_at FROM whales WHERE address = ?), ?
        ), ?)
        ON CONFLICT(address) DO UPDATE SET
            name = excluded.name,
            alpha_score = excluded.alpha_score,
            pnl = excluded.pnl,
            volume = excluded.volume,
            win_rate = excluded.win_rate,
            total_trades = excluded.total_trades,
            market_category = excluded.market_category,
            tags = excluded.tags,
            last_seen = excluded.last_seen,
            updated_at = excluded.updated_at
    """, (address, name, alpha_score, pnl, volume, win_rate, total_trades,
          market_category, tags, now, address, now, now))
    conn.commit()
    conn.close()


def add_signal(whale_address: str, whale_name: str, alpha_score: float,
               market_slug: str, market_title: str, market_category: str = "",
               condition_id: str = "", token_id: str = "", outcome: str = "",
               side: str = "", size: float = 0.0, price: float = 0.0,
               usd_value: float = 0.0, confidence: float = 0.0) -> int:
    """Add a whale signal. Returns signal ID or 0 if duplicate."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO whale_signals
                (whale_address, whale_name, alpha_score, market_slug,
                 market_title, market_category, condition_id, token_id, outcome, side,
                 size, price, usd_value, confidence, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(whale_address, market_slug, token_id, outcome, side)
            DO NOTHING
        """, (whale_address, whale_name, alpha_score, market_slug,
              market_title, market_category, condition_id, token_id, outcome, side,
              size, price, usd_value, confidence, now))
        conn.commit()
        return cursor.lastrowid or 0
    except Exception:
        return 0
    finally:
        conn.close()


def get_unsignaled_signals() -> list:
    """Get signals that haven't been consumed yet."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM whale_signals
        WHERE signaled = 0
        ORDER BY detected_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_signals_signaled(signal_ids: list) -> None:
    """Mark signals as consumed."""
    if not signal_ids:
        return
    conn = get_connection()
    conn.execute(
        "UPDATE whale_signals SET signaled = 1 WHERE id IN ({})".format(
            ",".join("?" for _ in signal_ids)
        ),
        signal_ids
    )
    conn.commit()
    conn.close()


def get_top_whales(limit: int = 20) -> list:
    """Get top whales by alpha score."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM whales
        WHERE alpha_score >= ?
        ORDER BY alpha_score DESC, pnl DESC
        LIMIT ?
    """, (70, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_position_seen(whale_address: str, asset_id: str, outcome: str) -> bool:
    """Check if a position was already seen (within TTL)."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("""
        SELECT 1 FROM seen_positions
        WHERE whale_address = ? AND asset_id = ? AND outcome = ?
        AND expires_at > ?
    """, (whale_address, asset_id, outcome, now)).fetchone()
    conn.close()
    return row is not None


def mark_position_seen(whale_address: str, asset_id: str,
                       outcome: str, ttl_hours: int = 24) -> None:
    """Mark a position as seen with an expiry TTL."""
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    conn = get_connection()
    conn.execute("""
        INSERT INTO seen_positions (whale_address, asset_id, outcome,
                                    seen_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(whale_address, asset_id, outcome) DO UPDATE SET
            seen_at = excluded.seen_at,
            expires_at = excluded.expires_at
    """, (whale_address, asset_id, outcome, now.isoformat(), expires))
    conn.commit()
    conn.close()


def log_scan(scan_type: str, wallets_found: int, signals_found: int,
             errors: str = "", duration: float = 0) -> None:
    """Log a scan result."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO scan_log (scan_time, scan_type, wallets_found,
                             signals_found, errors, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (datetime.now(timezone.utc).isoformat(), scan_type, wallets_found,
          signals_found, errors, duration))
    conn.commit()
    conn.close()


def get_stats() -> dict:
    """Get pipeline statistics."""
    conn = get_connection()
    whale_count = conn.execute("SELECT COUNT(*) FROM whales").fetchone()[0]
    signal_count = conn.execute("SELECT COUNT(*) FROM whale_signals").fetchone()[0]
    unsignaled = conn.execute(
        "SELECT COUNT(*) FROM whale_signals WHERE signaled = 0"
    ).fetchone()[0]
    conn.close()
    return {
        "whales": whale_count,
        "total_signals": signal_count,
        "unsignaled": unsignaled,
    }
