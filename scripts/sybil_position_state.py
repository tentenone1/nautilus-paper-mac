"""
Sybil Position State — tracks previous scan positions for delta detection.

State file: research/sybil_position_state.json
Position key: {condition_id}|{address}|{outcome}

Delta types: NEW, INCREASED (>20%), REDUCED (>50% drop), CLOSED, UNCHANGED
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "research" / "sybil_position_state.json"

# Delta thresholds
INCREASE_THRESHOLD = 1.2  # 20%+ growth
REDUCE_THRESHOLD = 0.5   # 50%+ drop


def position_key(condition_id: str, address: str, outcome: str) -> str:
    """Generate unique key for a position."""
    return f"{condition_id}|{address}|{outcome}"


def load_previous_state() -> dict[str, dict]:
    """Load previous scan's position state.
    
    Returns dict of {position_key: {size_usd, market_title, label, timestamp}}
    """
    if not STATE_FILE.exists():
        return {}
    
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("positions", {})
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load position state: {e}")
        return {}


def save_state(positions: dict[str, dict]) -> None:
    """Save current position state for next scan.
    
    Args:
        positions: Dict of {position_key: {size_usd, market_title, label}}
    """
    state = {
        "last_scan": datetime.now(timezone.utc).isoformat(),
        "position_count": len(positions),
        "positions": positions,
    }
    
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    
    logger.info(f"Saved position state: {len(positions)} positions")


def detect_delta(
    current_size: float,
    previous_size: Optional[float],
) -> str:
    """Detect position change delta.
    
    Returns: NEW, INCREASED, REDUCED, CLOSED, UNCHANGED
    """
    if previous_size is None:
        return "NEW"
    
    if current_size == 0:
        return "CLOSED"
    
    if previous_size == 0:
        return "NEW"
    
    ratio = current_size / previous_size
    
    if ratio >= INCREASE_THRESHOLD:
        return "INCREASED"
    
    if ratio <= REDUCE_THRESHOLD:
        return "REDUCED"
    
    return "UNCHANGED"


def compute_delta_summary(deltas: list[str]) -> dict[str, int]:
    """Count delta types."""
    summary = {
        "new_positions": 0,
        "increased": 0,
        "reduced": 0,
        "closed": 0,
        "unchanged": 0,
    }
    
    for d in deltas:
        key = d.lower()
        if key == "new":
            summary["new_positions"] += 1
        elif key in summary:
            summary[key] += 1
    
    return summary