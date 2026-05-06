"""Price Pump Tracker — Monitors price movements after whale signal entry events.

Phase 1: Signal entry detection hook. Captures trade entry events and
subscribes to price tracking for those markets.

Usage (hooked from whale_follower.py on_order_filled):
    from components.price_tracker import subscribe
    subscribe(market_id=..., signal_id=..., entry_price=..., whale_address=...)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("price_tracker")

# Path to the persistent events log
_EVENTS_PATH = Path(__file__).resolve().parent.parent / "data" / "pump_tracker_events.json"

# In-memory set of subscribed market IDs for idempotency
_subscribed_markets: set[str] = set()


def subscribe(
    market_id: str,
    signal_id: str,
    entry_price: float,
    whale_address: str,
    whale_name: str = "",
    market_title: str = "",
) -> None:
    """Subscribe a market to price pump tracking.

    Records the entry event and prevents duplicate subscriptions for the
    same market_id within the same process lifetime.

    Args:
        market_id: Market identifier (condition_id from Polymarket).
        signal_id: Unique trade/signal identifier (UUID from trade creation).
        entry_price: Entry price at subscription time.
        whale_address: On-chain wallet address of the whale.
        whale_name: Optional whale name for logging / identification.
        market_title: Optional market title for logging / identification.
    """
    if market_id in _subscribed_markets:
        logger.debug("Market %s already subscribed, skipping", market_id)
        return

    event: dict[str, object] = {
        "signal_id": signal_id,
        "market_id": market_id,
        "entry_price": entry_price,
        "whale_address": whale_address,
        "whale_name": whale_name or "",
        "market_title": market_title or "",
        "timestamp": time.time(),
        "event_type": "signal_entry",
    }

    _append_event(event)
    _subscribed_markets.add(market_id)

    logger.info(
        "Price tracker subscribed: market=%s signal=%s whale=%s entry=%.4f",
        market_id[:20],
        signal_id[:8],
        whale_name[:20] if whale_name else "?",
        entry_price,
    )


def get_subscribed_markets() -> list[str]:
    """Return list of currently subscribed market IDs."""
    return list(_subscribed_markets)


def get_events(market_id: Optional[str] = None) -> list[dict]:
    """Read all events from the pump tracker events file.

    Args:
        market_id: If provided, filter events to this market only.

    Returns:
        List of event dicts, newest first.
    """
    if not _EVENTS_PATH.exists():
        return []

    try:
        events: list[dict] = json.loads(_EVENTS_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read pump tracker events: %s", e)
        return []

    if market_id:
        events = [e for e in events if e.get("market_id") == market_id]

    events.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
    return events


def _append_event(event: dict) -> None:
    """Thread-safe append of an event dict to the events JSON file.

    Reads existing file, appends new event, writes back. Uses an
    exclusive-create lockfile to avoid corruption on concurrent writes.
    """
    _EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Simple file locking via mkdir (atomic on Linux)
    lock_dir = _EVENTS_PATH.with_suffix(".lock")
    _lock_path(lock_dir)
    try:
        events: list[dict] = []
        if _EVENTS_PATH.exists():
            try:
                content = _EVENTS_PATH.read_text()
                if content.strip():
                    events = json.loads(content)
            except (json.JSONDecodeError, OSError):
                events = []

        events.append(event)

        _EVENTS_PATH.write_text(json.dumps(events, indent=2))
    finally:
        _unlock_path(lock_dir)


def _lock_path(lock_dir: Path) -> None:
    """Acquire an exclusive lock by creating a directory atomically."""
    for attempt in range(10):
        try:
            lock_dir.mkdir(exist_ok=False)
            return
        except FileExistsError:
            time.sleep(0.05)
    logger.warning("Could not acquire lock for %s after 10 attempts", lock_dir)


def _unlock_path(lock_dir: Path) -> None:
    """Release the lock by removing the directory."""
    try:
        lock_dir.rmdir()
    except OSError:
        logger.warning("Failed to remove lock %s", lock_dir)
