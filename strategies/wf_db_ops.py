"""Whale Follower — Database operations.

Standalone functions for logging trades and recovering open positions
from the trades database. No class coupling — all state is passed as parameters.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


_DEFAULT_DB_PATH = Path(__file__).parent.parent / "research" / "trades.db"


def _ensure_db_schema(conn: sqlite3.Connection) -> None:
    """Create the trades table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            whale_name TEXT,
            whale_address TEXT,
            category TEXT NOT NULL,
            market_title TEXT,
            condition_id TEXT,
            token_id TEXT,
            side TEXT,
            entry_price REAL,
            exit_price REAL,
            position_size_usd REAL,
            kelly_fraction REAL,
            confidence REAL,
            edge_score REAL,
            signal_source TEXT,
            entry_reason TEXT,
            exit_reason TEXT,
            realized_pnl REAL,
            realized_return REAL,
            duration_seconds REAL,
            resolution_outcome TEXT,
            dispute_flag INTEGER DEFAULT 0,
            notes TEXT,
            instrument_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


def log_trade_to_db(
    *,
    trade_id: str | None = None,
    timestamp: str | None = None,
    whale_name: str,
    whale_address: str,
    market_title: str,
    side: str,
    entry_price: float,
    position_size_usd: float,
    category: str,
    signal_source: str = "whale_tracker",
    edge_score: float = 0.0,
    confidence: float = 0.0,
    kelly_fraction: float = 0.0,
    entry_reason: str = "",
    instrument_id: str = "",
    condition_id: str = "",
    db_path: str | Path | None = None,
    log_func=None,
) -> str | None:
    """Insert a new trade record into the trades database.

    Uses a transaction with explicit BEGIN/COMMIT and rollback on failure.

    Args:
        trade_id: UUID for the trade row. Auto-generated if None.
        timestamp: ISO-8601 timestamp string. Defaults to now (UTC).
        whale_name: Whale wallet name or identifier.
        whale_address: On-chain wallet address.
        market_title: Human-readable market title.
        side: "BUY" or "SELL".
        entry_price: Fill price per share.
        position_size_usd: Notional size in USD.
        category: Market category (e.g. "sports", "politics").
        signal_source: Source of the trading signal.
        edge_score: Calibrated edge score.
        confidence: Signal confidence (0–1).
        kelly_fraction: Applied Kelly fraction.
        entry_reason: Reason for entering the trade.
        instrument_id: Full instrument ID string.
        condition_id: Condition ID portion of the instrument ID.
        db_path: Path to trades.db. Defaults to research/trades.db.
        log_func: Optional logging callable for errors.

    Returns:
        The trade_id string on success, or None on failure.
    """
    conn = None
    db = Path(db_path) if db_path else _DEFAULT_DB_PATH
    db.parent.mkdir(parents=True, exist_ok=True)

    if trade_id is None:
        trade_id = str(uuid.uuid4())
    if timestamp is None:
        timestamp = str(datetime.now(timezone.utc))

    try:
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_db_schema(conn)

        conn.execute("BEGIN TRANSACTION")
        conn.execute("""
            INSERT OR IGNORE INTO trades (
                trade_id, timestamp, whale_name, whale_address,
                market_title, side, entry_price, position_size_usd,
                category, signal_source, edge_score, confidence,
                kelly_fraction, entry_reason, instrument_id, condition_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id,
            timestamp,
            whale_name,
            whale_address or whale_name,  # fallback to name if address empty
            market_title,
            side,
            entry_price,
            position_size_usd,
            category,
            signal_source,
            edge_score,
            confidence,
            kelly_fraction,
            entry_reason,
            instrument_id,
            condition_id,
        ))
        conn.execute("COMMIT")

        if log_func:
            log_func(
                f"[DB] Logged trade: {whale_name} | {category} | "
                f"{market_title[:40]} | ${position_size_usd:.0f}"
            )
        return trade_id

    except Exception as db_error:
        if conn:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        if log_func:
            log_func(
                f"[DB] Transaction failed, rolled back: {db_error} | "
                f"trade_id={trade_id} | whale={whale_name} | "
                f"market={market_title[:40]} | size=${position_size_usd:.0f} | "
                f"entry={entry_price:.4f}"
            )
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def recover_open_positions(
    db_path: str | Path | None = None,
    log_func=None,
    max_recovery_age_hours: float = 4.0,
) -> list[dict]:
    """Reload unfinished positions from the trades DB.

    Reads rows that have no exit_reason (i.e. still open) and reconstructs
    a list of position info dicts suitable for populating an _open_positions
    registry.

    Only recovers trades newer than ``max_recovery_age_hours``. Older orphans
    (from crashed runs) are skipped — they will be cleaned from the DB on the
    next successful exit or by admin maintenance. This prevents stale orphans
    from filling ``_open_positions`` and blocking ``max_open_positions``.

    Args:
        db_path: Path to trades.db. Defaults to research/trades.db.
        log_func: Optional logging callable.
        max_recovery_age_hours: Skip orphans older than this many hours.
            Default 4.0 (matches ``WhaleFollowerConfig.max_hold_hours``).

    Returns:
        List of position dicts, each with keys: inst_key, whale_name,
        market_title, category, side, entry_price, size, entry_time,
        trade_id, condition_id, venue_position_id, edge_score.
    """
    from datetime import datetime, timezone

    db = Path(db_path) if db_path else _DEFAULT_DB_PATH
    if not db.exists():
        if log_func:
            log_func("[RECOVER] No trades DB found, skipping recovery")
        return []

    conn = None
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT instrument_id, trade_id, whale_name, market_title, category, "
            "side, entry_price, position_size_usd, condition_id, edge_score, timestamp "
            "FROM trades WHERE exit_reason IS NULL "
            "AND instrument_id IS NOT NULL "
            "ORDER BY timestamp"
        ).fetchall()

        if not rows:
            if log_func:
                log_func("[RECOVER] No orphan positions to recover")
            return []

        now = datetime.now(timezone.utc)
        cutoff_seconds = max_recovery_age_hours * 3600
        recovered: list[dict] = []
        skipped = 0

        for row in rows:
            inst_id, trade_id, whale_name, market_title, category, side, entry_price, size, cond_id, edge_score, ts_str = row

            # ── Skip stale orphans (crashed sandbox runs) ──────────────
            try:
                ts = datetime.fromisoformat(ts_str)
                age_seconds = (now - ts).total_seconds()
                if age_seconds > cutoff_seconds:
                    if log_func:
                        log_func(
                            f"[RECOVER] SKIP stale orphan ({age_seconds/3600:.1f}h old): "
                            f"{whale_name or '?'} | {market_title[:40] if market_title else inst_id[:40]}"
                        )
                    skipped += 1
                    continue
            except (ValueError, TypeError):
                pass  # can't parse timestamp → include anyway (safer to recover)

            # Try to parse as InstrumentId; fall back to raw string
            try:
                from nautilus_trader.model.identifiers import InstrumentId
                inst_key = str(InstrumentId.from_str(inst_id))
            except Exception:
                inst_key = inst_id

            recovered.append({
                "inst_key": inst_key,
                "whale_name": whale_name or "unknown",
                "market_title": market_title or inst_id[:80],
                "category": category or "Unknown",
                "side": side or "BUY",
                "entry_price": entry_price or 0.5,
                "size": size or 0.0,
                "entry_time": time.time(),  # use current time so exit timer can age-check properly
                "trade_id": trade_id,
                "condition_id": cond_id or "",
                "venue_position_id": "",
                "edge_score": edge_score or 0.0,
            })

        if log_func:
            log_func(
                f"[RECOVER] Recovered {len(recovered)} open positions from DB"
            )
        return recovered

    except Exception as e:
        if log_func:
            log_func(f"[RECOVER] Failed to recover open positions: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
